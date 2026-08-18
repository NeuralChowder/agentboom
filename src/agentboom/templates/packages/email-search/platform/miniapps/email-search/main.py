"""Email-search mini-app — search and ask your mail
(agentboom package: email-search).

Two layers:
- /search — deterministic keyword search over the cache (subject,
  sender, body). Always works, no LLM needed.
- /ask — plain-language questions: the LLM picks keywords, the search
  runs, and the LLM answers from the results (citing subjects/dates).
  Degrades to a clear 503 without an LLM gateway.

Endpoints (mounted at /api/email-search/):
  GET  /health
  GET  /stats                 what is searchable
  POST /search                {text, account?, months_back?, limit?}
  POST /ask                   {question, months_back?}
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentboom_sdk import db
from agentboom_sdk.llm import complete, complete_json

log = logging.getLogger("miniapps.email-search")

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "app": "email-search"}


@router.get("/stats")
async def stats():
    total = await db.fetchval("SELECT count(*) FROM emails")
    span = await db.fetchone(
        "SELECT min(received_at) AS oldest, max(received_at) AS newest "
        "FROM emails")
    accounts = await db.fetchval(
        "SELECT count(DISTINCT account_id) FROM emails")
    return {"cached_emails": total, "mailboxes": accounts,
            "oldest": span["oldest"] if span else None,
            "newest": span["newest"] if span else None}


def _months_cutoff(months_back) -> str:
    months = max(1, min(int(months_back or 12), 60))
    return (datetime.now(timezone.utc)
            - timedelta(days=30 * months)).strftime("%Y-%m-%d %H:%M:%S")


@router.post("/search")
async def search(payload: dict):
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)
    limit = max(1, min(int(payload.get("limit") or 30), 100))
    cutoff = _months_cutoff(payload.get("months_back"))
    account = (payload.get("account") or "").strip().lower()

    # Every word must appear somewhere in the message (AND over terms,
    # OR over fields) — precise enough for mail, simple enough to trust.
    words = [w for w in text.lower().split() if w][:6]
    clauses, params = [], []
    for word in words:
        like = f"%{word}%"
        clauses.append(
            "(lower(e.subject) LIKE ? OR lower(e.from_email) LIKE ? "
            "OR lower(e.from_name) LIKE ? OR lower(e.body_text) LIKE ?)")
        params.extend([like, like, like, like])
    where = ["e.received_at > ?", " AND ".join(clauses)]
    params = [cutoff, *params]
    if account:
        where.append("a.email = ?")
        params.append(account)

    rows = await db.fetchall(
        f"""
        SELECT e.id, a.email AS account_email, e.from_email, e.from_name,
               e.subject, e.received_at, e.has_attachment,
               substr(e.body_text, 1, 220) AS snippet
        FROM emails e JOIN email_accounts a ON a.id = e.account_id
        WHERE {' AND '.join(where)}
        ORDER BY e.received_at DESC
        LIMIT ?
        """,
        (*params, limit))
    return {"count": len(rows), "results": rows}


@router.post("/ask")
async def ask(payload: dict):
    question = (payload.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "question is required"}, status_code=400)
    months_back = payload.get("months_back") or 12

    keywords = await complete_json(
        "Extract 1-5 search keywords for this question about someone's "
        f"email archive. Reply with JSON only: {{\"keywords\": [\"...\"]}}\n"
        f"Question: {question}",
        temperature=0.0, max_tokens=100, timeout=60)
    if keywords is None:
        return JSONResponse(
            {"error": "asking needs the LLM gateway (LLM_BASE_URL/LLM_API_KEY) "
                      "— use /search for keyword search"},
            status_code=503)
    found = []
    for kw in (keywords.get("keywords") or [])[:5]:
        batch = await search({"text": str(kw), "months_back": months_back,
                              "limit": 15})
        if isinstance(batch, dict) and batch.get("results"):
            found.extend(batch["results"])
    # Dedupe by id, keep date order.
    seen, corpus = set(), []
    for row in sorted(found, key=lambda r: r.get("received_at") or "",
                      reverse=True):
        if row["id"] not in seen:
            seen.add(row["id"])
            corpus.append(row)
    corpus = corpus[:25]
    if not corpus:
        return {"answer": "Nothing matching in the cached mail for the "
                          f"last {months_back} months.", "sources": []}

    listing = "\n".join(
        f"- [{r['received_at']}] {r['subject']} — from {r['from_name'] or r['from_email']}"
        f" ({r['account_email']})\n  snippet: {r['snippet']}"
        for r in corpus)
    answer = await complete(
        f"Question about the user's email archive:\n{question}\n\n"
        f"Matching messages:\n{listing}\n\n"
        "Answer using ONLY these messages. Be concise, cite subjects and "
        "dates. If they do not contain the answer, say so.",
        temperature=0.2, max_tokens=800, timeout=120)
    return {"answer": answer or "The LLM returned no answer.",
            "sources": corpus}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
