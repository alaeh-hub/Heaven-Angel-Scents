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
7. The current date is {current_date} (Philippine time, UTC+8) — also given as snapshot.as_of.date. \
"Today" means exactly this date. Use only fields scoped to today (today_sales, today_by_branch, \
today_hq_sales) when asked about today's sales. Never assume the newest rows in recent_sales or \
recent_activity are from today — check each row's own date against {current_date} first, since \
they may be from a prior day if nothing has sold yet today.

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
    return str(session.get("user_id") or get_remote_address())

def _ai_chat_rate_limit():
    return current_app.config.get("AI_CHAT_RATE_LIMIT", "15 per minute;150 per day")

def _current_business_date():
    row = query("SELECT CURDATE() AS today, NOW() AS now_ts", fetchone=True)
    return row["today"], row["now_ts"]

def _stock_request_items_by_request(request_ids):
    """Group stock_request_items rows by request_id, with product name/unit
    resolved. Shared by both snapshot builders below (admin sees pending
    requests across every branch, branch staff see just their own) since
    a delivery's line-item shape — name, unit, qty, price — is identical
    either way.

    A stock request is a *delivery* now (see stock_request_items) — it
    can carry any number of SKUs, each with its own qty and its own price
    snapshotted at request time. There's no sku/requested_qty column on
    stock_requests itself anymore, so this can't be read off the header
    row the way an older, one-item-per-request version of this snapshot
    used to.
    """
    if not request_ids:
        return {}
    placeholders = ",".join(["%s"] * len(request_ids))
    item_rows = query(
        f"""SELECT sri.request_id, p.item_name, p.unit, sri.requested_qty, sri.unit_price
            FROM stock_request_items sri JOIN products p ON sri.sku = p.sku
            WHERE sri.request_id IN ({placeholders})
            ORDER BY sri.request_id, p.item_name""",
        tuple(request_ids),
    )
    items_by_request = {}
    for row in item_rows:
        items_by_request.setdefault(row["request_id"], []).append({
            "item_name": row["item_name"],
            "unit": row["unit"],
            "requested_qty": row["requested_qty"],
            "unit_price": row["unit_price"],
        })
    return items_by_request

def _admin_snapshot():
    stats = {
        "sku_count": query("SELECT COUNT(*) c FROM products", fetchone=True)["c"],
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
    # A stock request is a delivery header now — item_count/total_qty come
    # from stock_request_items, and each delivery's own products/quantities/
    # prices are attached below via _stock_request_items_by_request() so the
    # assistant can still answer "what's on order for Manila" without the
    # old assumption that one request == one SKU.
    pending_headers = query(
        """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at, b.branch_name,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                  COALESCE(SUM(sri.requested_qty * sri.unit_price), 0) AS total_value
           FROM stock_requests sr
           JOIN branches b ON sr.branch_id = b.branch_id
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
           WHERE sr.status = 'Pending'
           GROUP BY sr.request_id, sr.delivery_number, sr.status, sr.requested_at, b.branch_name
           ORDER BY sr.requested_at ASC LIMIT 12"""
    )
    items_by_request = _stock_request_items_by_request(
        [h["request_id"] for h in pending_headers])
    pending_requests = [
        {
            "delivery_number": h["delivery_number"],
            "branch_name": h["branch_name"],
            "status": h["status"],
            "requested_at": h["requested_at"],
            "item_count": h["item_count"],
            "total_qty": h["total_qty"],
            "total_value": h["total_value"],
            "items": items_by_request.get(h["request_id"], []),
        }
        for h in pending_headers
    ]
    revenue_by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(s.qty_sold), 0) AS units_sold,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b LEFT JOIN sales s ON b.branch_id = s.branch_id
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )

    hq_sales = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(s.qty_sold), 0) AS units_sold,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b LEFT JOIN sales s ON b.branch_id = s.branch_id
           WHERE b.is_hq = TRUE GROUP BY b.branch_id, b.branch_name""",
        fetchone=True,
    )
    today_by_branch = query(
        """SELECT b.branch_name,
                  COALESCE(SUM(s.qty_sold), 0) AS units_sold,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM branches b
           LEFT JOIN sales s ON b.branch_id = s.branch_id AND DATE(s.sold_at) = CURDATE()
           WHERE b.is_hq = FALSE GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"""
    )
    today_hq_sales = query(
        """SELECT COALESCE(SUM(s.qty_sold), 0) AS units_sold,
                  COALESCE(SUM(s.qty_sold * s.unit_price), 0) AS revenue
           FROM sales s JOIN branches b ON s.branch_id = b.branch_id
           WHERE b.is_hq = TRUE AND DATE(s.sold_at) = CURDATE()""",
        fetchone=True,
    )
    return {
        "stats": stats,
        "low_stock_across_branches": low_stock,
        "pending_stock_requests": pending_requests,
        "revenue_by_branch": revenue_by_branch,
        "hq_sales": hq_sales,
        "today_by_branch": today_by_branch,
        "today_hq_sales": today_hq_sales,
    }

def _branch_snapshot(branch_id):
    inventory = query(
        """SELECT p.sku, p.item_name, p.unit, bi.stock_qty, bi.reorder_level, p.price AS price
           FROM branch_inventory bi JOIN products p ON bi.sku = p.sku
           WHERE bi.branch_id = %s ORDER BY p.item_name""",
        (branch_id,),
    )
    low_stock = [row for row in inventory if row["stock_qty"] <= row["reorder_level"]]
    pending_headers = query(
        """SELECT sr.request_id, sr.delivery_number, sr.status, sr.requested_at,
                  COUNT(sri.item_id) AS item_count,
                  COALESCE(SUM(sri.requested_qty), 0) AS total_qty,
                  COALESCE(SUM(sri.requested_qty * sri.unit_price), 0) AS total_value
           FROM stock_requests sr
           LEFT JOIN stock_request_items sri ON sri.request_id = sr.request_id
           WHERE sr.branch_id = %s AND sr.status IN ('Pending', 'In Transit')
           GROUP BY sr.request_id, sr.delivery_number, sr.status, sr.requested_at
           ORDER BY sr.requested_at DESC""",
        (branch_id,),
    )
    items_by_request = _stock_request_items_by_request(
        [h["request_id"] for h in pending_headers])
    pending_requests = [
        {
            "delivery_number": h["delivery_number"],
            "status": h["status"],
            "requested_at": h["requested_at"],
            "item_count": h["item_count"],
            "total_qty": h["total_qty"],
            "total_value": h["total_value"],
            "items": items_by_request.get(h["request_id"], []),
        }
        for h in pending_headers
    ]
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

    today, now_ts = _current_business_date()
    snapshot["as_of"] = {"date": str(today), "datetime": str(now_ts), "timezone": "Asia/Manila (UTC+8)"}

    system_instruction = SYSTEM_PROMPT.format(
        scope_label=scope_label,
        current_date=today,
        snapshot_json=json.dumps(snapshot, default=_json_default, indent=2),
    )

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
