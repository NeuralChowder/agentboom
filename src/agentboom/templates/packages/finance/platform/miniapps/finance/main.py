"""Finance mini-app — money tracking shaped by your own rules
(agentboom package: finance).

Everything is a runtime resource — the same install serves personal
spending, subscription tracking, business income, client billing:

- categories: your vocabulary (income/expense)
- rules: turn matching mail into transactions (sender/subject/body
  substrings, optional fixed amount, optional category)
- transactions: pending -> confirmed/ignored; amounts come from rules,
  the LLM, or you

Mail integration is opportunistic: with the email package, incoming
mail that matches a rule becomes a pending transaction; without it,
everything still works manually.

Endpoints (mounted at /api/finance/):
  GET    /health
  GET    /categories            POST /categories
  PUT    /categories/{id}       DELETE /categories/{id}
  GET    /rules                 POST /rules          POST /rules/test
  PUT    /rules/{id}            DELETE /rules/{id}
  GET    /transactions          POST /transactions
  PUT    /transactions/{id}     DELETE /transactions/{id}
  POST   /transactions/{id}/confirm     POST /transactions/{id}/ignore
  POST   /classify              (manifest job target)
  GET    /stats?months=6
"""
import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db
from agentboom_sdk.llm import complete_json

log = logging.getLogger("miniapps.finance")

router = APIRouter()

CURRENCY = os.environ.get("FINANCE_DEFAULT_CURRENCY", "EUR")


# ── helpers ────────────────────────────────────────────────────────


def _lower(value) -> str:
    return (value or "").lower()


def _rule_matches(rule: dict, from_email: str, subject: str, body: str) -> bool:
    hit_from = rule["match_from"] and _lower(rule["match_from"]) in _lower(from_email)
    hit_subject = rule["match_subject"] and _lower(rule["match_subject"]) in _lower(subject)
    hit_body = rule["contains"] and _lower(rule["contains"]) in _lower(body)
    return bool(hit_from or hit_subject or hit_body)


# ── email integration (harmless when the email package is absent) ──


async def handle_event(event: dict) -> None:
    if event.get("type") != "email.received":
        return
    data = event.get("data") or {}
    rules = await db.fetchall(
        "SELECT * FROM finance_rules WHERE enabled = 1")
    if not rules:
        return
    body = ""
    if data.get("email_id"):
        try:
            row = await db.fetchone(
                "SELECT body_text FROM emails WHERE id = ?", data["email_id"])
            body = (row or {}).get("body_text") or ""
        except Exception:  # noqa: BLE001 — emails table absent without the email package
            pass
    for rule in rules:
        if not _rule_matches(dict(rule), data.get("from_email", ""),
                             data.get("subject", ""), body):
            continue
        # One transaction per email per rule; the email id dedupes.
        existing = await db.fetchone(
            "SELECT id FROM finance_transactions WHERE email_id = ? AND rule_id = ?",
            (data.get("email_id"), rule["id"]))
        if existing:
            continue
        await db.execute(
            "INSERT INTO finance_transactions "
            "(category_id, rule_id, amount, currency, description, source, "
            " email_id, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, 'email', ?, CURRENT_TIMESTAMP)",
            (rule["category_id"], rule["id"], rule["amount_hint"], CURRENCY,
             f"{data.get('subject') or '(no subject)'} — {data.get('from_email')}",
             data.get("email_id")))
        log.info("finance: rule '%s' matched mail from %s",
                 rule["name"], data.get("from_email"))
        break  # first matching rule wins


# ── categories ─────────────────────────────────────────────────────


@router.get("/health")
async def health():
    return {"status": "ok", "app": "finance", "currency": CURRENCY}


@router.get("/categories")
async def list_categories():
    rows = await db.fetchall(
        "SELECT c.*, (SELECT count(*) FROM finance_transactions t "
        " WHERE t.category_id = c.id) AS transactions "
        "FROM finance_categories c ORDER BY c.name")
    return {"categories": rows}


@router.post("/categories")
async def add_category(payload: dict):
    name = (payload.get("name") or "").strip().lower()
    kind = payload.get("kind") or "expense"
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if kind not in ("income", "expense"):
        return JSONResponse({"error": "kind must be income|expense"}, status_code=400)
    if await db.fetchone("SELECT id FROM finance_categories WHERE name = ?", name):
        return JSONResponse({"error": "category exists"}, status_code=409)
    await db.execute(
        "INSERT INTO finance_categories (name, kind, note) VALUES (?, ?, ?)",
        (name, kind, (payload.get("note") or "").strip() or None))
    row = await db.fetchone("SELECT * FROM finance_categories WHERE name = ?", name)
    return {"ok": True, "category": dict(row)}


@router.put("/categories/{category_id}")
async def update_category(category_id: int, payload: dict):
    row = await db.fetchone(
        "SELECT * FROM finance_categories WHERE id = ?", category_id)
    if not row:
        return JSONResponse({"error": "no such category"}, status_code=404)
    name = (payload.get("name") or row["name"]).strip().lower()
    kind = payload.get("kind") or row["kind"]
    if kind not in ("income", "expense"):
        return JSONResponse({"error": "kind must be income|expense"}, status_code=400)
    await db.execute(
        "UPDATE finance_categories SET name = ?, kind = ?, "
        "note = COALESCE(?, note) WHERE id = ?",
        (name, kind, payload.get("note"), category_id))
    return {"ok": True}


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int):
    removed = await db.execute(
        "DELETE FROM finance_categories WHERE id = ?", category_id)
    if not removed:
        return JSONResponse({"error": "no such category"}, status_code=404)
    return {"deleted": True, "note": "transactions keep their history, uncategorised"}


# ── rules ──────────────────────────────────────────────────────────


@router.get("/rules")
async def list_rules():
    rows = await db.fetchall(
        "SELECT r.*, c.name AS category FROM finance_rules r "
        "LEFT JOIN finance_categories c ON c.id = r.category_id "
        "ORDER BY r.id")
    return {"rules": rows}


@router.post("/rules")
async def add_rule(payload: dict):
    name = (payload.get("name") or "").strip()
    match_from = (payload.get("match_from") or "").strip()
    match_subject = (payload.get("match_subject") or "").strip()
    contains = (payload.get("contains") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if not (match_from or match_subject or contains):
        return JSONResponse(
            {"error": "give at least one of match_from / match_subject / contains"},
            status_code=400)
    if await db.fetchone("SELECT id FROM finance_rules WHERE name = ?", name):
        return JSONResponse({"error": "rule exists"}, status_code=409)
    category_id = None
    if payload.get("category"):
        cat = await db.fetchone(
            "SELECT id FROM finance_categories WHERE name = ?",
            str(payload["category"]).strip().lower())
        if not cat:
            return JSONResponse(
                {"error": f"unknown category '{payload['category']}' — create it first"},
                status_code=400)
        category_id = cat["id"]
    await db.execute(
        "INSERT INTO finance_rules "
        "(name, category_id, match_from, match_subject, contains, amount_hint) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, category_id, match_from or None, match_subject or None,
         contains or None, payload.get("amount_hint")))
    row = await db.fetchone("SELECT * FROM finance_rules WHERE name = ?", name)
    return {"ok": True, "rule": dict(row)}


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: int, payload: dict):
    row = await db.fetchone("SELECT * FROM finance_rules WHERE id = ?", rule_id)
    if not row:
        return JSONResponse({"error": "no such rule"}, status_code=404)
    if "enabled" in payload:
        await db.execute("UPDATE finance_rules SET enabled = ? WHERE id = ?",
                         (1 if payload.get("enabled") else 0, rule_id))
    for field in ("match_from", "match_subject", "contains", "amount_hint"):
        if field in payload:
            await db.execute(
                f"UPDATE finance_rules SET {field} = ? WHERE id = ?",
                (payload.get(field), rule_id))
    if payload.get("category"):
        cat = await db.fetchone(
            "SELECT id FROM finance_categories WHERE name = ?",
            str(payload["category"]).strip().lower())
        if cat:
            await db.execute(
                "UPDATE finance_rules SET category_id = ? WHERE id = ?",
                (cat["id"], rule_id))
    return {"ok": True}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: int):
    removed = await db.execute("DELETE FROM finance_rules WHERE id = ?", rule_id)
    if not removed:
        return JSONResponse({"error": "no such rule"}, status_code=404)
    return {"deleted": True}


@router.post("/rules/test")
async def test_rule(payload: dict):
    """Which enabled rule would match this message — try before you save."""
    rules = await db.fetchall("SELECT * FROM finance_rules WHERE enabled = 1")
    for rule in rules:
        if _rule_matches(dict(rule), payload.get("from", ""),
                         payload.get("subject", ""), payload.get("body", "")):
            return {"matched": dict(rule)}
    return {"matched": None}


# ── transactions ───────────────────────────────────────────────────


@router.get("/transactions")
async def list_transactions(status: str = "", category: str = "",
                            limit: int = 50):
    limit = max(1, min(int(limit), 500))
    where, params = ["1=1"], []
    if status:
        where.append("t.status = ?")
        params.append(status)
    if category:
        where.append("c.name = ?")
        params.append(category.strip().lower())
    rows = await db.fetchall(
        f"""
        SELECT t.*, c.name AS category, r.name AS rule_name
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        LEFT JOIN finance_rules r ON r.id = t.rule_id
        WHERE {' AND '.join(where)}
        ORDER BY t.occurred_at DESC, t.id DESC
        LIMIT ?
        """,
        (*params, limit))
    return {"transactions": rows}


@router.post("/transactions")
async def add_transaction(payload: dict):
    description = (payload.get("description") or "").strip()
    if not description:
        return JSONResponse({"error": "description is required"}, status_code=400)
    category_id = None
    if payload.get("category"):
        cat = await db.fetchone(
            "SELECT id FROM finance_categories WHERE name = ?",
            str(payload["category"]).strip().lower())
        if not cat:
            return JSONResponse({"error": "unknown category"}, status_code=400)
        category_id = cat["id"]
    insert_sql = (
        "INSERT INTO finance_transactions "
        "(category_id, amount, currency, direction, description, status, "
        " occurred_at) VALUES (?, ?, ?, ?, ?, ?, "
        "COALESCE(?, CURRENT_TIMESTAMP))")
    insert_params = (
        category_id, payload.get("amount"),
        (payload.get("currency") or CURRENCY).upper(),
        payload.get("direction"), description,
        "confirmed" if payload.get("amount") else "pending",
        payload.get("occurred_at"))
    if db.is_postgres():
        row = await db.fetchone(insert_sql + " RETURNING *", insert_params)
    else:
        await db.execute(insert_sql, insert_params)
        row = await db.fetchone(
            "SELECT * FROM finance_transactions WHERE id = last_insert_rowid()")
    return {"ok": True, "transaction": dict(row)}


@router.put("/transactions/{tx_id}")
async def update_transaction(tx_id: int, payload: dict):
    row = await db.fetchone(
        "SELECT * FROM finance_transactions WHERE id = ?", tx_id)
    if not row:
        return JSONResponse({"error": "no such transaction"}, status_code=404)
    if "amount" in payload:
        await db.execute("UPDATE finance_transactions SET amount = ? WHERE id = ?",
                         (payload.get("amount"), tx_id))
    if "direction" in payload and payload["direction"] in ("in", "out"):
        await db.execute(
            "UPDATE finance_transactions SET direction = ? WHERE id = ?",
            (payload["direction"], tx_id))
    if payload.get("category"):
        cat = await db.fetchone(
            "SELECT id FROM finance_categories WHERE name = ?",
            str(payload["category"]).strip().lower())
        if cat:
            await db.execute(
                "UPDATE finance_transactions SET category_id = ? WHERE id = ?",
                (cat["id"], tx_id))
    if payload.get("description"):
        await db.execute(
            "UPDATE finance_transactions SET description = ? WHERE id = ?",
            (str(payload["description"]).strip(), tx_id))
    return {"ok": True}


@router.delete("/transactions/{tx_id}")
async def delete_transaction(tx_id: int):
    removed = await db.execute(
        "DELETE FROM finance_transactions WHERE id = ?", tx_id)
    if not removed:
        return JSONResponse({"error": "no such transaction"}, status_code=404)
    return {"deleted": True}


@router.post("/transactions/{tx_id}/confirm")
async def confirm_transaction(tx_id: int):
    updated = await db.execute(
        "UPDATE finance_transactions SET status = 'confirmed' WHERE id = ?", tx_id)
    if not updated:
        return JSONResponse({"error": "no such transaction"}, status_code=404)
    return {"ok": True}


@router.post("/transactions/{tx_id}/ignore")
async def ignore_transaction(tx_id: int):
    updated = await db.execute(
        "UPDATE finance_transactions SET status = 'ignored' WHERE id = ?", tx_id)
    if not updated:
        return JSONResponse({"error": "no such transaction"}, status_code=404)
    return {"ok": True}


# ── classification (manifest job target) ───────────────────────────


@router.post("/classify")
async def classify_pending(limit: int = 10):
    """Fill amount/direction/category on pending transactions via the LLM.

    Degrades gracefully: without an LLM gateway the items stay pending
    for manual confirmation — nothing is ever guessed silently.
    """
    limit = max(1, min(int(limit), 50))
    pending = await db.fetchall(
        "SELECT * FROM finance_transactions WHERE status = 'pending' "
        "ORDER BY id LIMIT ?", limit)
    if not pending:
        return {"ok": True, "classified": 0, "note": "nothing pending"}
    categories = await db.fetchall("SELECT name, kind FROM finance_categories")
    cat_list = ", ".join(f"{c['name']} ({c['kind']})" for c in categories) or "(none yet)"
    classified = 0
    for tx in pending:
        verdict = await complete_json(
            "Extract the money facts from this transaction record.\n"
            f"Description: {tx['description']}\n"
            f"Known categories: {cat_list}\n\n"
            "Reply with JSON only: {\"amount\": number|null, "
            "\"direction\": \"in\"|\"out\"|null, \"category\": \"name\"|null}\n"
            "Use null for anything you cannot determine.",
            temperature=0.0, max_tokens=200, timeout=60)
        if verdict is None:
            break  # no LLM — stop, leave the rest for manual review
        updates, params = [], []
        if verdict.get("amount") is not None:
            try:
                updates.append("amount = ?")
                params.append(float(verdict["amount"]))
            except (TypeError, ValueError):
                pass
        if verdict.get("direction") in ("in", "out"):
            updates.append("direction = ?")
            params.append(verdict["direction"])
        if verdict.get("category"):
            cat = await db.fetchone(
                "SELECT id FROM finance_categories WHERE name = ?",
                str(verdict["category"]).strip().lower())
            if cat:
                updates.append("category_id = ?")
                params.append(cat["id"])
        if updates:
            await db.execute(
                f"UPDATE finance_transactions SET {', '.join(updates)} WHERE id = ?",
                (*params, tx["id"]))
            classified += 1
    log.info("finance: classified %d/%d pending transaction(s)",
             classified, len(pending))
    return {"ok": True, "classified": classified, "pending_seen": len(pending)}


# ── stats ──────────────────────────────────────────────────────────


@router.get("/stats")
async def stats(months: int = 6):
    months = max(1, min(int(months), 36))
    # Cutoff computed here, not in SQL — datetime('now', ...) is
    # SQLite-only and the same code must run on PostgreSQL agents.
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=30 * months)).strftime("%Y-%m-%d %H:%M:%S")
    totals = await db.fetchall(
        """
        SELECT direction, COALESCE(c.name, '(uncategorised)') AS category,
               count(*) AS n, COALESCE(sum(t.amount), 0) AS total,
               t.currency
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.status = 'confirmed' AND t.amount IS NOT NULL
          AND t.occurred_at >= ?
        GROUP BY direction, category, currency
        ORDER BY total DESC
        """,
        (cutoff,))
    pending = await db.fetchval(
        "SELECT count(*) FROM finance_transactions WHERE status = 'pending'")
    return {"months": months, "by_category": totals, "pending": pending,
            "currency_default": CURRENCY}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
