"""
AI Assistant — a read-only Q&A helper backed by the Gemini API.

Design intent (read this before changing scope):
- The model NEVER touches the database and NEVER runs SQL. All it ever
  sees is a small JSON "snapshot" that THIS route builds using the exact
  same parameterized queries and session-based scoping as the normal
  admin/branch dashboards (Admin -> all branches, Branch -> only their
  own branch_id from the session).
- The system prompt tells the model its limits, but the prompt is not
  what enforces them — the enforcement is that a Branch user's snapshot
  simply never contains another branch's rows, so there's nothing to
  leak even if someone tries to talk the model into it.
- The model has no tool-calling / function-calling access, so it cannot
  record sales, dispatch stock, change prices, or write anything back.
  It's a pure conversational read-only assistant over a fixed snapshot.
- The chat endpoint is rate-limited per signed-in user (see AI_CHAT_RATE_LIMIT
  in config.py) since every call is a billed Gemini API request.
"""
import datetime
import decimal
import json

import requests
from flask import Blueprint, current_app, jsonify, render_template, request, session

from db import query
from decorators import login_required
from extensions import limiter
from flask_limiter.util import get_remote_address

bp = Blueprint("ai", __name__, url_prefix="/ai")

GEMINI_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You are the "H&A Assistant", a read-only helper built into the internal \
inventory system for Heaven & Angel Scents, a perfume brand with an HQ warehouse and retail \
branches.

Who you're talking to right now: {scope_label}

Hard rules, always:
1. You may only use the DATA SNAPSHOT below. It already reflects exactly what this user is \
allowed to see in the app — nothing has been hidden from you on purpose beyond that.
2. If asked about anything not in the snapshot (numbers for another branch, HQ production \
detail if you're a Branch user, anything you're unsure of), say plainly that you don't have \
access to that here, and suggest who could help (an HQ Admin, or the relevant page in the app).
3. Never invent SKUs, quantities, prices, or names that are not in the snapshot.
4. You cannot perform actions. You cannot record a sale, dispatch or request stock, change a \
price, create an account, or edit anything. If asked to do something, name the sidebar page \
that does it instead (e.g. "Record Sale", "Request Stock", "Branch Stock").
5. Keep answers short, concrete, and specific to this business. No generic filler.
6. Reply in plain text only — no markdown. Do not use asterisks, underscores, backticks, \
hash headers, or bullet/numbered list syntax. For a page name, just write it plainly, e.g. \
Record Sale, not **Record Sale**. If you need to list a few things, put each on its own line \
with a dash and a space, e.g. "- Item name".

DATA SNAPSHOT (current as of this message):
{snapshot_json}
"""


def _json_default(o):
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return float(o)
    return str(o)


def _rate_limit_key():
    """Rate-limit per signed-in user rather than per IP.

    Several branch cashiers can share one office's public IP, and one
    admin can be behind a NAT with many other people — keying on
    user_id keeps the limit meaningful per account instead of
    accidentally throttling (or failing to throttle) whole offices.
    """
    return str(session.get("user_id") or get_remote_address())


def _ai_chat_rate_limit():
    return current_app.config.get("AI_CHAT_RATE_LIMIT", "15 per minute;150 per day")


# ---------------------------------------------------------------- snapshots
def _admin_snapshot():
    stats = {
        "active_skus": query("SELECT COUNT(*) c FROM products WHERE is_active = TRUE", fetchone=True)["c"],
        "branches": query("SELECT COUNT(*) c FROM branches WHERE is_hq = FALSE", fetchone=True)["c"],
        "pending_requests": query(
            "SELECT COUNT(*) c FROM stock_requests WHERE status = 'Pending'", fetchone=True
        )["c"],
    }
    low_stock = query(
        """SELECT b.branch_name, p.sku, p.item_name, p.unit, bi.stock_qty, bi.reorder_level
           FROM branch_inventory bi
           JOIN branches b ON bi.branch_id = b.branch_id
           JOIN products p ON bi.sku = p.sku
           WHERE b.is_hq = FALSE AND bi.stock_qty <= bi.reorder_level
           ORDER BY bi.stock_qty ASC LIMIT 12"""
    )
    pending_requests = query(
        """SELECT b.branch_name, p.sku, p.item_name, p.unit, sr.requested_qty, sr.requested_at
           FROM stock_requests sr
           JOIN branches b ON sr.branch_id = b.branch_id
           JOIN products p ON sr.sku = p.sku
           WHERE sr.status = 'Pending'
           ORDER BY sr.requested_at ASC LIMIT 12"""
    )
    revenue_by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(s.qty_sold), 0) AS units_sold,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b LEFT JOIN sales s ON b.branch_id = s.branch_id
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )
    # revenue_by_branch above is retail branches only (b.is_hq = FALSE), so
    # sales rung up directly at the HQ warehouse (branch_id = the is_hq
    # branch, via routes/admin.py's Record Sale page) never showed up
    # anywhere in this snapshot. Surface them as their own block instead
    # of folding them into revenue_by_branch, so the assistant can tell
    # "sold from HQ" apart from "sold from a branch" rather than treating
    # HQ as just another branch row.
    hq_sales = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(s.qty_sold), 0) AS units_sold,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b LEFT JOIN sales s ON b.branch_id = s.branch_id
           WHERE b.is_hq = TRUE GROUP BY b.branch_id, b.branch_name""",
        fetchone=True,
    )
    return {
        "stats": stats,
        "low_stock_across_branches": low_stock,
        "pending_stock_requests": pending_requests,
        "revenue_by_branch": revenue_by_branch,
        "hq_sales": hq_sales,
    }


def _branch_snapshot(branch_id):
    inventory = query(
        """SELECT p.sku, p.item_name, p.unit, bi.stock_qty, bi.reorder_level, p.price AS price
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s AND p.is_active = TRUE ORDER BY p.item_name""",
        (branch_id,),
    )
    low_stock = [row for row in inventory if row["stock_qty"] <= row["reorder_level"]]
    pending_requests = query(
        """SELECT p.sku, p.item_name, p.unit, sr.requested_qty, sr.status, sr.requested_at
           FROM stock_requests sr JOIN products p ON sr.sku = p.sku
           WHERE sr.branch_id = %s AND sr.status IN ('Pending', 'In Transit')
           ORDER BY sr.requested_at DESC""",
        (branch_id,),
    )
    today_sales = query(
        """SELECT COALESCE(SUM(qty_sold), 0) AS units, COALESCE(SUM(qty_sold * unit_price), 0) AS revenue
           FROM sales WHERE branch_id = %s AND DATE(sold_at) = CURDATE()""",
        (branch_id,), fetchone=True,
    )
    recent_sales = query(
        """SELECT p.sku, p.item_name, p.unit, s.qty_sold, s.unit_price, s.sold_at
           FROM sales s JOIN products p ON s.sku = p.sku
           WHERE s.branch_id = %s ORDER BY s.sold_at DESC LIMIT 8""",
        (branch_id,),
    )
    return {
        "inventory": inventory,
        "low_stock": low_stock,
        "pending_requests": pending_requests,
        "today_sales": today_sales,
        "recent_sales": recent_sales,
    }


# ---------------------------------------------------------------- gemini call
def _call_gemini(system_instruction, contents):
    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        return None, "The AI assistant isn't configured yet — ask an admin to set GEMINI_API_KEY."

    model = current_app.config.get("GEMINI_MODEL", "gemini-2.5-flash")
    payload = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2000},
    }

    try:
        resp = requests.post(
            GEMINI_URL_TMPL.format(model=model),
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None, "Couldn't reach the AI service. Please try again in a moment."
    except ValueError:
        return None, "The AI service returned an unexpected response."

    candidates = data.get("candidates") or []
    if not candidates:
        return None, "The assistant didn't return a response. Please rephrase and try again."

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        return None, "The assistant returned an empty response."
    return text, None


# ---------------------------------------------------------------- routes
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

    role = session.get("role")
    if role == "Admin":
        snapshot = _admin_snapshot()
        scope_label = "an HQ Admin — can see all branches, at a summary level"
    else:
        snapshot = _branch_snapshot(session.get("branch_id"))
        scope_label = f"Branch staff at {session.get('branch_name')} — can only see this branch's own data"

    system_instruction = SYSTEM_PROMPT.format(
        scope_label=scope_label,
        snapshot_json=json.dumps(snapshot, default=_json_default, indent=2),
    )

    # Client-supplied history is only ever used as conversational text —
    # it never changes what data the model can see (that's rebuilt above,
    # fresh and scoped, on every single call).
    contents = []
    for turn in history_in[-10:]:
        turn_role = "model" if turn.get("role") == "model" else "user"
        text = str(turn.get("text", ""))[:2000].strip()
        if text:
            contents.append({"role": turn_role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": user_message}]})

    reply, error = _call_gemini(system_instruction, contents)
    if error:
        return jsonify(error=error), 502
    return jsonify(reply=reply)
