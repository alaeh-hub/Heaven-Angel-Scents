import datetime
import decimal
import json

import requests
from flask import (Blueprint, abort, current_app, flash, jsonify,
                   redirect, render_template, request, session, url_for)
from flask_limiter.util import get_remote_address

from db import query, execute, transaction
from decorators import login_required
from extensions import limiter
from utils import ValidationError

import routes.ai_tools as ai_tools

try:
    import audit
except ImportError:  # pragma: no cover
    audit = None

try:
    import sockets
except ImportError:  # pragma: no cover
    sockets = None

bp = Blueprint("ai", __name__, url_prefix="/ai")

GEMINI_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# MAX_TOOL_ROUNDS caps how many times the model can call a tool before we
# force a final answer — protects against a runaway loop (and the Gemini
# API cost that comes with it) if the model keeps asking for more tools
# instead of answering.
MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """You are the "H&A Assistant", a helper built into the internal inventory \
system for Heaven & Angel Scents, a perfume brand with an HQ warehouse and retail branches.

Who you're talking to right now: {scope_label}
Today's date: {current_date} (Philippine time, UTC+8). "Today" means exactly this date.

You have tools to look up live data — use them instead of guessing or relying on anything from \
earlier in the conversation, since stock levels and requests change constantly. Call a tool \
whenever a question depends on current numbers (stock, pending deliveries, sales). Don't call a \
tool for something you were just told in this same conversation's tool results.

Rules, always:
1. Never invent SKUs, quantities, prices, branch names, or dates that a tool didn't return to you.
2. If a tool returns an error or empty results, say so plainly — don't paper over it or guess.
3. You can propose a stock request with the propose_stock_request tool, but that ONLY creates a \
draft — it is never sent anywhere and never changes real inventory. A human still has to review \
and approve it on the Drafts page before it becomes a real delivery. Always tell the user it's a \
draft awaiting approval, and mention the Drafts page, when you use this tool.
4. Other than proposing a draft, you cannot perform actions — you cannot record a sale, dispatch \
stock, change a price, or create an account. If asked to do something else, name the sidebar page \
that does it (e.g. Record Sale, Request Stock, Branch Stock).
5. Keep answers short, concrete, and specific to this business. No generic filler.
6. Reply in plain text only — no markdown. No asterisks, underscores, backticks, hash headers, or \
bullet/numbered list syntax. For a page name, just write it plainly, e.g. Record Sale. If you need \
to list a few things, put each on its own line with a dash and a space, e.g. "- Item name".
"""


def _json_default(o):
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


def _rate_limit_key():
    return str(session.get("user_id") or get_remote_address())


def _ai_chat_rate_limit():
    return current_app.config.get("AI_CHAT_RATE_LIMIT", "15 per minute;150 per day")


def _current_business_date():
    row = query("SELECT CURDATE() AS today", fetchone=True)
    return row["today"]


def _ctx():
    role = session.get("role")
    return {
        "role": role,
        "branch_id": session.get("branch_id"),
        "branch_name": session.get("branch_name"),
        "user_id": session.get("user_id"),
        "username": session.get("username"),
    }


def _call_gemini_raw(payload):
    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        return None, "The AI assistant isn't configured yet — ask an admin to set GEMINI_API_KEY."

    model = current_app.config.get("GEMINI_MODEL", "gemini-3.5-flash")
    try:
        # requests' json= kwarg calls json.dumps() internally with no way
        # to pass a custom `default=`, so anything not natively
        # JSON-serializable (Decimal from a DB row's price/qty column,
        # date/datetime from a timestamp column) would blow up here with
        # a raw TypeError. Serialize it ourselves with _json_default and
        # send the resulting string via data= instead, so those values
        # get converted (Decimal -> float, date/datetime -> isoformat)
        # instead of crashing the request.
        body = json.dumps(payload, default=_json_default)
        resp = requests.post(
            GEMINI_URL_TMPL.format(model=model),
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": api_key},
            data=body,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json(), None
    except requests.RequestException:
        return None, "Couldn't reach the AI service. Please try again in a moment."
    except ValueError:
        return None, "The AI service returned an unexpected response."


def _run_agent(system_instruction, contents, tools, ctx):
    """The tool-calling loop: send contents+tools to Gemini; if it asks for
    one or more function calls, run them locally (scoped by ctx), append
    both the model's call and our results back into `contents`, and ask
    again — up to MAX_TOOL_ROUNDS times. Returns (final_text, error)."""
    for _ in range(MAX_TOOL_ROUNDS):
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": contents,
            "tools": tools,
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200},
        }
        data, error = _call_gemini_raw(payload)
        if error:
            return None, error

        candidates = data.get("candidates") or []
        if not candidates:
            return None, "The assistant didn't return a response. Please rephrase and try again."

        model_content = candidates[0].get("content", {}) or {}
        parts = model_content.get("parts", []) or []
        function_calls = [p["functionCall"]
                          for p in parts if "functionCall" in p]

        if not function_calls:
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                return None, "The assistant returned an empty response."
            return text, None

        # Model wants tool(s) run. Echo its own turn back verbatim, then
        # answer with one functionResponse per call it made.
        contents.append({"role": "model", "parts": parts})
        response_parts = []
        for call in function_calls:
            name = call.get("name")
            args = call.get("args") or {}
            result = ai_tools.dispatch(name, args, ctx)
            fr = {"name": name, "response": result}
            if call.get("id"):
                fr["id"] = call["id"]
            response_parts.append({"functionResponse": fr})
        contents.append({"role": "user", "parts": response_parts})

    # Exhausted MAX_TOOL_ROUNDS and the model was still calling tools on
    # the last round — but every result it asked for is already sitting
    # in `contents`. Rather than throwing that away, make one final call
    # with tools omitted so the model can't ask for yet another round
    # and has to answer from what it's already gathered.
    final_payload = {
        "system_instruction": {"parts": [{"text": system_instruction + (
            "\n\nYou are out of tool calls for this turn. Do not request any "
            "more — answer now using only the tool results already above, "
            "and say plainly if something you'd still need wasn't fetched."
        )}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1200},
    }
    data, error = _call_gemini_raw(final_payload)
    if error:
        return None, error

    candidates = data.get("candidates") or []
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", []) or []
        text = "".join(p.get("text", "") for p in parts).strip()
        if text:
            return text, None

    return None, "That took too many steps to answer — try asking something more specific."


@bp.route("/")
@login_required
def chat_page():
    return render_template("ai/chat.html")


@bp.route("/chat", methods=["POST"])
@login_required
@limiter.limit(_ai_chat_rate_limit, key_func=_rate_limit_key)
def chat():
    payload = request.get_json(silent=True) or {}
    user_message = str(payload.get("message", "")).strip()
    history_in = payload.get("history") or []

    if not user_message:
        return jsonify(error="Message can't be empty."), 400
    if len(user_message) > 1000:
        return jsonify(error="Keep messages under 1000 characters."), 400

    ctx = _ctx()
    if ctx["role"] == "Admin":
        scope_label = "an HQ Admin — can see and act across every branch"
    else:
        scope_label = f"Branch staff at {ctx['branch_name']} — can only see and act on this branch's own data"

    today = _current_business_date()
    system_instruction = SYSTEM_PROMPT.format(
        scope_label=scope_label, current_date=today)
    tools = ai_tools.get_tool_declarations(ctx["role"])

    contents = []
    for turn in history_in[-10:]:
        turn_role = "model" if turn.get("role") == "model" else "user"
        text = str(turn.get("text", ""))[:2000].strip()
        if text:
            contents.append({"role": turn_role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    reply, error = _run_agent(system_instruction, contents, tools, ctx)
    if error:
        return jsonify(error=error), 502
    return jsonify(reply=reply)


# ---------------------------------------------------------------------------
# Draft review — the human-in-the-loop step for propose_stock_request.
#
# delivery_number: confirmed against the real schema.sql — every delivery
# is numbered DR-<request_id zero-padded to 6 digits>, generated right
# after insert (see schema.sql section 6 and its migration in section 12).
# Using request_id here (not draft_id) is what makes an AI-originated
# delivery numbered identically to one requested by hand.

def _next_delivery_number(request_id):
    return f"DR-{request_id:06d}"


def _visible_drafts(ctx):
    if ctx["role"] == "Admin":
        return query(
            """SELECT d.draft_id, d.status, d.note, d.reasoning, d.created_at,
                      d.created_by_username, d.resulting_request_id, b.branch_name
               FROM ai_stock_drafts d JOIN branches b ON d.branch_id = b.branch_id
               ORDER BY d.created_at DESC LIMIT 50"""
        )
    return query(
        """SELECT d.draft_id, d.status, d.note, d.reasoning, d.created_at,
                  d.created_by_username, d.resulting_request_id, b.branch_name
           FROM ai_stock_drafts d JOIN branches b ON d.branch_id = b.branch_id
           WHERE d.branch_id = %s ORDER BY d.created_at DESC LIMIT 50""",
        (ctx["branch_id"],),
    )


def _draft_or_404(draft_id, ctx):
    row = query(
        "SELECT * FROM ai_stock_drafts WHERE draft_id = %s", (draft_id,), fetchone=True
    )
    if not row:
        abort(404)
    if ctx["role"] != "Admin" and row["branch_id"] != ctx["branch_id"]:
        abort(403)
    return row


@bp.route("/drafts")
@login_required
def list_drafts():
    ctx = _ctx()
    drafts = _visible_drafts(ctx)
    for d in drafts:
        d["items"] = query(
            """SELECT sdi.sku, sdi.suggested_qty, p.item_name, p.unit
               FROM ai_stock_draft_items sdi JOIN products p ON sdi.sku = p.sku
               WHERE sdi.draft_id = %s""",
            (d["draft_id"],),
        )
    return render_template("ai/drafts.html", drafts=drafts)


@bp.route("/drafts/<int:draft_id>/approve", methods=["POST"])
@login_required
def approve_draft(draft_id):
    ctx = _ctx()
    draft = _draft_or_404(draft_id, ctx)
    if draft["status"] != "Pending Review":
        flash("That draft has already been reviewed.", "error")
        return redirect(url_for("ai.list_drafts"))

    items = query(
        "SELECT sku, suggested_qty FROM ai_stock_draft_items WHERE draft_id = %s", (
            draft_id,)
    )
    if not items:
        flash("Draft has no items — can't approve.", "error")
        return redirect(url_for("ai.list_drafts"))

    skus = [i["sku"] for i in items]
    placeholders = ",".join(["%s"] * len(skus))
    prices = {
        r["sku"]: r["price"]
        for r in query(f"SELECT sku, price FROM products WHERE sku IN ({placeholders})", tuple(skus))
    }

    try:
        with transaction() as conn:
            cur = conn.cursor()
            # delivery_number is DR-<request_id>, so request_id has to exist
            # first. Insert with a placeholder that's unique even under
            # concurrent approvals (stock_requests.delivery_number is
            # UNIQUE), then update it to the real DR-###### once we have
            # the row's own id, still inside this same transaction.
            cur.execute(
                """INSERT INTO stock_requests (branch_id, delivery_number, status, requested_at)
                   VALUES (%s, CONCAT('TMP-', CONNECTION_ID(), '-', UNIX_TIMESTAMP()), 'Pending', NOW())""",
                (draft["branch_id"],),
            )
            request_id = cur.lastrowid
            delivery_number = _next_delivery_number(request_id)
            cur.execute(
                "UPDATE stock_requests SET delivery_number = %s WHERE request_id = %s",
                (delivery_number, request_id),
            )
            cur.executemany(
                "INSERT INTO stock_request_items (request_id, sku, requested_qty, unit_price) VALUES (%s, %s, %s, %s)",
                [(request_id, i["sku"], i["suggested_qty"],
                  prices.get(i["sku"], 0)) for i in items],
            )
            cur.execute(
                """UPDATE ai_stock_drafts SET status = 'Approved', resulting_request_id = %s,
                   reviewed_by_user_id = %s, reviewed_at = NOW() WHERE draft_id = %s""",
                (request_id, ctx["user_id"], draft_id),
            )
            cur.close()
    except Exception:
        current_app.logger.exception(
            "Failed to approve AI draft #%s", draft_id)
        flash("Couldn't approve that draft — please try again.", "error")
        return redirect(url_for("ai.list_drafts"))

    if audit:
        audit.log_action("approve_ai_stock_request", target=delivery_number,
                         details=f"from draft #{draft_id}")
    if sockets:
        try:
            sockets.notify_admin_and_branch(
                draft["branch_id"], ["requests", "ai_drafts"])
        except Exception:
            current_app.logger.exception(
                "Failed to push realtime notice for approved draft")

    flash(f"Draft approved as delivery {delivery_number}.", "success")
    return redirect(url_for("ai.list_drafts"))


@bp.route("/drafts/<int:draft_id>/reject", methods=["POST"])
@login_required
def reject_draft(draft_id):
    ctx = _ctx()
    draft = _draft_or_404(draft_id, ctx)
    if draft["status"] != "Pending Review":
        flash("That draft has already been reviewed.", "error")
        return redirect(url_for("ai.list_drafts"))

    execute(
        """UPDATE ai_stock_drafts SET status = 'Rejected',
           reviewed_by_user_id = %s, reviewed_at = NOW() WHERE draft_id = %s""",
        (ctx["user_id"], draft_id),
    )
    if audit:
        audit.log_action("reject_ai_stock_request",
                         target=f"draft #{draft_id}")
    if sockets:
        try:
            sockets.notify_admin_and_branch(draft["branch_id"], ["ai_drafts"])
        except Exception:
            current_app.logger.exception(
                "Failed to push realtime notice for rejected draft")

    flash("Draft rejected.", "success")
    return redirect(url_for("ai.list_drafts"))
