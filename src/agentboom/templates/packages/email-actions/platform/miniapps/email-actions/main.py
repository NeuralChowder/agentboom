"""Email-actions mini-app — the "needs attention" queue
(agentboom package: email-actions).

Every email.received event queues a triage item. A triage pass (job,
every 5 minutes, or POST /triage) asks the LLM which messages actually
need the user and drafts reply options for each. The queue is the
user's inbox zero surface: execute a proposal to send its reply, or
done/skip to settle the message. Sending always happens through the
mailbox's own SMTP, only on an explicit /execute call.

Without an LLM gateway configured, triage degrades gracefully: messages
still queue (needs_attention, no drafts) instead of being lost.

Endpoints (mounted at /api/email-actions/):
  GET  /health
  GET  /queue?limit=            triaged items waiting on the user
  GET  /items/{id}              item + email + proposals
  GET  /items/{id}/message      the cached email body
  POST /triage                  run a triage pass now
  POST /items/{id}/execute      {proposal_id} — send the reply NOW
  POST /items/{id}/redraft      {instructions} — rewrite with the LLM
  POST /items/{id}/done         settled
  POST /items/{id}/skip         not worth answering
  GET  /stats
"""
import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db
from agentboom_sdk.llm import complete_json
from connectors.email import EmailError, send_for_account

log = logging.getLogger("miniapps.email-actions")

router = APIRouter()

_TRIAGE_SYSTEM = (
    "You triage email for a busy person. Decide whether a message needs "
    "a decision or reply from them, and draft reply options when it does. "
    "Be strict: newsletters, notifications, receipts and anything purely "
    "informational do NOT need attention."
)


def _triage_prompt(msg: dict) -> str:
    return (
        f"From: {msg['from_name']} <{msg['from_email']}>\n"
        f"Subject: {msg['subject']}\n\n"
        f"{(msg['body_text'] or '')[:4000]}\n\n"
        "Reply with JSON only:\n"
        '{"needs_attention": bool, "reason": "one short sentence for the user", '
        '"urgency": 1-5, "proposals": [{"label": "short verb phrase", '
        '"stance": "accept|decline|info", "body": "complete reply email text, '
        'signed naturally", "rationale": "why this option", '
        '"needs_confirmation": true}]}\n'
        "proposals may be empty when nothing needs answering."
    )


# ── event subscription (wired by the manifest `subscribes`) ────────


async def handle_event(event: dict) -> None:
    if event.get("type") != "email.received":
        return
    data = event.get("data") or {}
    email_id = data.get("email_id")
    if not email_id:
        return
    await db.execute(
        "INSERT OR IGNORE INTO attention_items (email_id, account_email) "
        "VALUES (?, ?)",
        (email_id, data.get("account_email")))


# ── endpoints ──────────────────────────────────────────────────────


@router.get("/health")
async def health():
    pending = await db.fetchval(
        "SELECT count(*) FROM attention_items WHERE status IN ('pending', 'triaged')")
    return {"status": "ok", "app": "email-actions", "open_items": pending}


@router.get("/queue")
async def queue(limit: int = 25):
    limit = max(1, min(int(limit), 100))
    rows = await db.fetchall(
        """
        SELECT i.id, i.email_id, i.account_email, i.reason, i.urgency,
               i.triaged_at, e.subject, e.from_name, e.from_email,
               e.received_at, e.has_attachment
        FROM attention_items i JOIN emails e ON e.id = i.email_id
        WHERE i.status = 'triaged' AND i.needs_attention = 1
        ORDER BY i.urgency DESC, e.received_at DESC
        LIMIT ?
        """, limit)
    items = []
    for row in rows:
        item = dict(row)
        item["proposals"] = await db.fetchall(
            "SELECT id, label, stance, body, rationale, needs_confirmation "
            "FROM reply_proposals WHERE item_id = ? ORDER BY id", item["id"])
        items.append(item)
    return {"items": items}


@router.get("/items/{item_id}")
async def one_item(item_id: int):
    item = await db.fetchone(
        "SELECT * FROM attention_items WHERE id = ?", item_id)
    if not item:
        return JSONResponse({"error": "no such item"}, status_code=404)
    email_row = await db.fetchone(
        "SELECT * FROM emails WHERE id = ?", item["email_id"])
    proposals = await db.fetchall(
        "SELECT * FROM reply_proposals WHERE item_id = ? ORDER BY id", item_id)
    return {"item": dict(item), "email": dict(email_row) if email_row else None,
            "proposals": proposals}


@router.get("/items/{item_id}/message")
async def item_message(item_id: int):
    item = await db.fetchone(
        "SELECT email_id FROM attention_items WHERE id = ?", item_id)
    if not item:
        return JSONResponse({"error": "no such item"}, status_code=404)
    email_row = await db.fetchone(
        "SELECT subject, from_name, from_email, received_at, body_text "
        "FROM emails WHERE id = ?", item["email_id"])
    return dict(email_row) if email_row else {"error": "email not found"}


@router.post("/triage")
async def triage_now(limit: int = 10):
    """Triage pass over pending items. Also the manifest job target."""
    limit = max(1, min(int(limit), 50))
    pending = await db.fetchall(
        "SELECT i.id, i.email_id FROM attention_items i "
        "WHERE i.status = 'pending' ORDER BY i.id LIMIT ?", limit)
    triaged = degraded = 0
    for item in pending:
        msg = await db.fetchone(
            "SELECT * FROM emails WHERE id = ?", item["email_id"])
        if not msg:
            await db.execute(
                "UPDATE attention_items SET status = 'skipped', "
                "settled_at = CURRENT_TIMESTAMP WHERE id = ?", item["id"])
            continue
        verdict = await complete_json(
            _triage_prompt(dict(msg)), system=_TRIAGE_SYSTEM,
            temperature=0.2, max_tokens=1500, timeout=120)
        if verdict is None:
            # No LLM (or it flaked): keep the message visible, undrafted.
            await db.execute(
                "UPDATE attention_items SET status = 'triaged', "
                "needs_attention = 1, urgency = 3, "
                "reason = 'no triage available — review manually', "
                "triaged_at = CURRENT_TIMESTAMP WHERE id = ?", item["id"])
            degraded += 1
            continue
        needs = 1 if verdict.get("needs_attention") else 0
        await db.execute(
            "UPDATE attention_items SET status = 'triaged', "
            "needs_attention = ?, reason = ?, urgency = ?, "
            "triaged_at = CURRENT_TIMESTAMP WHERE id = ?",
            (needs, str(verdict.get("reason") or "")[:500],
             max(1, min(int(verdict.get("urgency") or 3), 5)), item["id"]))
        if needs:
            for prop in (verdict.get("proposals") or [])[:4]:
                body = str(prop.get("body") or "").strip()
                if not body:
                    continue
                await db.execute(
                    "INSERT INTO reply_proposals "
                    "(item_id, label, stance, body, rationale, needs_confirmation) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (item["id"], str(prop.get("label") or "Reply")[:80],
                     str(prop.get("stance") or "info")[:20], body,
                     str(prop.get("rationale") or "")[:500],
                     1 if prop.get("needs_confirmation", True) else 0))
        triaged += 1
    log.info("email-actions: triaged %d item(s), %d degraded", triaged, degraded)
    return {"ok": True, "triaged": triaged, "degraded": degraded}


@router.post("/items/{item_id}/execute")
async def execute_proposal(item_id: int, payload: dict):
    """Send the chosen proposal's reply. This actually sends email."""
    item = await db.fetchone(
        "SELECT * FROM attention_items WHERE id = ?", item_id)
    if not item:
        return JSONResponse({"error": "no such item"}, status_code=404)
    if item["status"] in ("done", "skipped"):
        return JSONResponse({"error": f"item already {item['status']}"},
                            status_code=409)
    proposal = await db.fetchone(
        "SELECT * FROM reply_proposals WHERE id = ? AND item_id = ?",
        (payload.get("proposal_id"), item_id))
    if not proposal:
        return JSONResponse({"error": "no such proposal for this item"},
                            status_code=404)
    email_row = await db.fetchone(
        "SELECT e.*, a.smtp_host, a.smtp_port FROM emails e "
        "JOIN email_accounts a ON a.id = e.account_id WHERE e.id = ?",
        item["email_id"])
    if not email_row:
        return JSONResponse({"error": "underlying email/account gone"},
                            status_code=404)
    body = proposal["body"]
    subject = email_row["subject"] or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    try:
        await send_for_account(dict(email_row), [email_row["from_email"]],
                               subject, body)
    except EmailError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)
    await db.execute(
        "UPDATE attention_items SET status = 'done', "
        "settled_at = CURRENT_TIMESTAMP WHERE id = ?", item_id)
    log.info("email-actions: replied to '%s' (to %s)",
             subject[:60], email_row["from_email"])
    return {"ok": True, "sent_to": email_row["from_email"], "subject": subject}


@router.post("/items/{item_id}/redraft")
async def redraft(item_id: int, payload: dict):
    instructions = (payload.get("instructions") or "").strip()
    if not instructions:
        return JSONResponse({"error": "instructions are required"},
                            status_code=400)
    item = await db.fetchone(
        "SELECT * FROM attention_items WHERE id = ?", item_id)
    if not item:
        return JSONResponse({"error": "no such item"}, status_code=404)
    msg = await db.fetchone(
        "SELECT * FROM emails WHERE id = ?", item["email_id"])
    if not msg:
        return JSONResponse({"error": "underlying email gone"}, status_code=404)
    previous = await db.fetchall(
        "SELECT label, body FROM reply_proposals WHERE item_id = ? ORDER BY id",
        item_id)
    prompt = (
        f"{_triage_prompt(dict(msg))}\n\n"
        f"Previous drafts:\n{json.dumps([dict(p) for p in previous], ensure_ascii=False)}\n\n"
        f"The user says: {instructions}\n"
        "Rewrite ONE reply draft accordingly. Reply with JSON only:\n"
        '{"label": "...", "stance": "accept|decline|info", "body": "...", '
        '"rationale": "...", "needs_confirmation": true}'
    )
    draft = await complete_json(prompt, system=_TRIAGE_SYSTEM,
                                temperature=0.4, max_tokens=1200, timeout=120)
    if draft is None:
        return JSONResponse(
            {"error": "no LLM gateway configured (LLM_BASE_URL/LLM_API_KEY)"},
            status_code=503)
    await db.execute(
        "INSERT INTO reply_proposals "
        "(item_id, label, stance, body, rationale, needs_confirmation) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, str(draft.get("label") or "Redraft")[:80],
         str(draft.get("stance") or "info")[:20],
         str(draft.get("body") or ""),
         str(draft.get("rationale") or "")[:500],
         1 if draft.get("needs_confirmation", True) else 0))
    return {"ok": True, "draft": draft}


@router.post("/items/{item_id}/done")
async def mark_done(item_id: int):
    updated = await db.execute(
        "UPDATE attention_items SET status = 'done', "
        "settled_at = CURRENT_TIMESTAMP WHERE id = ? AND status != 'done'",
        item_id)
    if not updated:
        return JSONResponse({"error": "no such open item"}, status_code=404)
    return {"ok": True}


@router.post("/items/{item_id}/skip")
async def skip_item(item_id: int):
    updated = await db.execute(
        "UPDATE attention_items SET status = 'skipped', "
        "settled_at = CURRENT_TIMESTAMP WHERE id = ? AND status != 'skipped'",
        item_id)
    if not updated:
        return JSONResponse({"error": "no such open item"}, status_code=404)
    return {"ok": True}


@router.get("/stats")
async def stats():
    rows = await db.fetchall(
        "SELECT status, count(*) AS n FROM attention_items GROUP BY status")
    return {"items": {r["status"]: r["n"] for r in rows},
            "proposals": await db.fetchval("SELECT count(*) FROM reply_proposals")}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
