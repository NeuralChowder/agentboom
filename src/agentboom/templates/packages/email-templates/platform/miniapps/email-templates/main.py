"""Email-templates mini-app — the look of everything that leaves the mailboxes
(agentboom package: email-templates).

Per-mailbox HTML templates with a footer, seasonal picks, agent-drafted
designs, one-click activation. Sending applies the active template
automatically (the engine lives in the email connector's send path); this
app manages the library and exposes the `email.render` capability so any
caller — including Node skills — can wrap a message without
re-implementing the renderer.

Endpoints (mounted at /api/email-templates/):
  GET    /health
  GET    /default                    the built-in default template
  GET    /templates                  library + who uses each
  POST   /templates                  {name, html, description?, season?, scope_email?}
  PUT    /templates/{id}             rename / edit / re-season
  DELETE /templates/{id}             (its mailboxes fall back to default)
  GET    /mailboxes                  each mailbox + its active template
  GET    /options/mailboxes          dropdown options
  POST   /templates/{id}/activate    {account_email}
  POST   /deactivate                 {account_email}  -> back to default
  GET    /preview?id={template_id}   sample message in this template
  GET    /preview-active?account=    sample in a mailbox's active template
  POST   /render                     {account_email, body, html?} -> wrapped HTML
  POST   /generate                   {instructions, scope_email?} -> agent writes one
  POST   /templates/{id}/redraft     {instructions} -> agent rewrites as a new variant
"""
import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from agentboom_sdk import db
from agentboom_sdk.llm import complete
from connectors.email.templates import (
    DEFAULT_FOOTER,
    DEFAULT_TEMPLATE,
    render,
    text_to_html,
    wrap,
)

log = logging.getLogger("miniapps.email-templates")

router = APIRouter()

_SAMPLE_BODY = (
    "<p style=\"margin:0 0 12px;\">Hi there,</p>"
    "<p style=\"margin:0 0 12px;\">This is how your message will look inside "
    "this template. The white card holds the message; the small grey line "
    "below is the footer.</p>"
    "<p style=\"margin:0;\">Best regards,<br>Your agent</p>"
)


def _norm(email) -> str:
    return (email or "").strip().lower()


async def _mailboxes() -> list:
    try:
        rows = await db.fetchall(
            "SELECT email, label FROM email_accounts ORDER BY email")
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001 — email package not present
        return []


async def _active_map() -> dict:
    """account_email -> template name."""
    try:
        rows = await db.fetchall(
            "SELECT a.account_email, t.name FROM email_template_active a "
            "JOIN email_templates t ON t.id = a.template_id")
        return {r["account_email"]: r["name"] for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def _validate_html(html: str) -> None:
    if "{{body}}" not in html:
        raise ValueError(
            "template must contain a {{body}} placeholder where the message goes")


# ── library ──────────────────────────────────────────────────────────


@router.get("/health")
async def health():
    count = await db.fetchval("SELECT count(*) FROM email_templates")
    return {"status": "ok", "app": "email-templates", "templates": count,
            "footer_configured": bool(DEFAULT_FOOTER)}


@router.get("/default")
async def default_template():
    return {"name": "(built-in default)", "html": DEFAULT_TEMPLATE,
            "footer": DEFAULT_FOOTER}


@router.get("/templates")
async def list_templates():
    rows = await db.fetchall("SELECT * FROM email_templates ORDER BY name")
    active = await _active_map()
    used_by = {}
    for account, name in active.items():
        used_by.setdefault(name, []).append(account)
    out = []
    for r in rows:
        d = dict(r)
        d["scope"] = r["scope_email"] or "All mailboxes"
        d["active_for"] = used_by.get(r["name"], [])
        out.append(d)
    return {"templates": out}


@router.post("/templates")
async def create_template(payload: dict):
    name = (payload.get("name") or "").strip()
    html = payload.get("html") or ""
    if not name or not html:
        return JSONResponse({"error": "name and html are required"}, status_code=400)
    try:
        _validate_html(html)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if await db.fetchone("SELECT id FROM email_templates WHERE name = ?", name):
        return JSONResponse({"error": "template exists"}, status_code=409)
    scope = _norm(payload.get("scope_email")) or None
    await db.execute(
        "INSERT INTO email_templates (name, scope_email, html, description, season, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, scope, html, (payload.get("description") or "").strip() or None,
         (payload.get("season") or "").strip() or None,
         payload.get("created_by") or "user"))
    row = await db.fetchone("SELECT * FROM email_templates WHERE name = ?", name)
    log.info("email-templates: created '%s'", name)
    return {"ok": True, "template": dict(row)}


@router.put("/templates/{template_id}")
async def update_template(template_id: int, payload: dict):
    row = await db.fetchone("SELECT * FROM email_templates WHERE id = ?", template_id)
    if not row:
        return JSONResponse({"error": "no such template"}, status_code=404)
    if payload.get("html"):
        try:
            _validate_html(payload["html"])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await db.execute("UPDATE email_templates SET html = ?, "
                         "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (payload["html"], template_id))
    for field in ("name", "description", "season"):
        if field in payload:
            await db.execute(
                f"UPDATE email_templates SET {field} = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                ((payload.get(field) or "").strip() or None, template_id))
    return {"ok": True}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: int):
    removed = await db.execute("DELETE FROM email_templates WHERE id = ?", template_id)
    if not removed:
        return JSONResponse({"error": "no such template"}, status_code=404)
    return {"deleted": True,
            "note": "mailboxes using it went back to the built-in default"}


# ── mailboxes + activation ───────────────────────────────────────────


@router.get("/mailboxes")
async def list_mailboxes():
    mailboxes = await _mailboxes()
    active = await _active_map()
    return {"mailboxes": [
        {**m, "template": active.get(m["email"], "(built-in default)")}
        for m in mailboxes
    ]}


@router.get("/options/mailboxes")
async def mailbox_options():
    mailboxes = await _mailboxes()
    return {"options": [
        {"value": m["email"], "label": f"{m['label']} <{m['email']}>"}
        for m in mailboxes
    ]}


@router.post("/templates/{template_id}/activate")
async def activate_template(template_id: int, payload: dict):
    account = _norm(payload.get("account_email"))
    if not account:
        return JSONResponse({"error": "account_email is required"}, status_code=400)
    row = await db.fetchone("SELECT * FROM email_templates WHERE id = ?", template_id)
    if not row:
        return JSONResponse({"error": "no such template"}, status_code=404)
    # A mailbox-specific template can only be activated on its own mailbox;
    # a shared template (scope NULL) can be activated anywhere.
    if row["scope_email"] and row["scope_email"] != account:
        return JSONResponse(
            {"error": f"template belongs to {row['scope_email']}, not {account}"},
            status_code=400)
    await db.execute(
        "INSERT INTO email_template_active (account_email, template_id) "
        "VALUES (?, ?) ON CONFLICT(account_email) DO UPDATE SET "
        "template_id = EXCLUDED.template_id, activated_at = CURRENT_TIMESTAMP",
        (account, template_id))
    log.info("email-templates: %s now sends with '%s'", account, row["name"])
    return {"ok": True, "account_email": account, "template": row["name"]}


@router.post("/deactivate")
async def deactivate(payload: dict):
    account = _norm(payload.get("account_email"))
    if not account:
        return JSONResponse({"error": "account_email is required"}, status_code=400)
    await db.execute("DELETE FROM email_template_active WHERE account_email = ?", account)
    return {"ok": True, "account_email": account, "template": "(built-in default)"}


# ── preview + render ─────────────────────────────────────────────────


@router.get("/preview", response_class=HTMLResponse)
async def preview_template(id: int):
    row = await db.fetchone("SELECT html FROM email_templates WHERE id = ?", id)
    if not row:
        return JSONResponse({"error": "no such template"}, status_code=404)
    return wrap(_SAMPLE_BODY, row["html"])


@router.get("/preview-active", response_class=HTMLResponse)
async def preview_active(account: str):
    html = await render("This is a sample message.", _norm(account))
    return html


@router.post("/render")
async def render_message(payload: dict):
    """The email.render capability: wrap a message for a mailbox."""
    account = _norm(payload.get("account_email"))
    body = payload.get("body") or ""
    if not body and not payload.get("html"):
        return JSONResponse({"error": "body (or html) is required"}, status_code=400)
    html = await render(body, account, payload.get("html"))
    return {"ok": True, "account_email": account, "html": html}


# ── agent-drafted templates ─────────────────────────────────────────


_GEN_SYSTEM = (
    "You write clean, elegant HTML email templates. You MUST include the "
    "literal placeholder {{body}} where the message goes, and you MAY "
    "include {{footer}} for the small footer line. Use inline CSS only "
    "(email-safe). Return ONLY the raw HTML, no prose, no code fences."
)


async def _generate_html(instructions: str) -> str:
    html = await complete(
        f"Write an HTML email template: {instructions}",
        system=_GEN_SYSTEM, temperature=0.5, max_tokens=2000, timeout=120)
    html = (html or "").strip()
    # strip accidental code fences
    if html.startswith("```"):
        html = html.strip("`")
        if html.lower().startswith("html"):
            html = html[4:]
    _validate_html(html)
    return html


@router.post("/generate")
async def generate_template(payload: dict):
    instructions = (payload.get("instructions") or "").strip()
    if not instructions:
        return JSONResponse({"error": "instructions are required"}, status_code=400)
    try:
        html = await _generate_html(instructions)
    except ValueError as exc:
        return JSONResponse({"error": f"the model produced no usable template: {exc}"},
                            status_code=502)
    except Exception as exc:  # noqa: BLE001 — no LLM configured / failure
        return JSONResponse({"error": str(exc)[:300]}, status_code=503)
    # Save inactive: nothing changes until it is previewed and activated.
    base = (instructions.split(".")[0][:40] or "template").strip().lower().replace(" ", "-")
    name = base
    n = 1
    while await db.fetchone("SELECT id FROM email_templates WHERE name = ?", name):
        n += 1
        name = f"{base}-{n}"
    scope = _norm(payload.get("scope_email")) or None
    await db.execute(
        "INSERT INTO email_templates (name, scope_email, html, description, created_by) "
        "VALUES (?, ?, ?, ?, 'agent')",
        (name, scope, html, instructions[:300]))
    row = await db.fetchone("SELECT * FROM email_templates WHERE name = ?", name)
    return {"ok": True, "template": dict(row),
            "note": "saved inactive — preview it, then activate it on a mailbox"}


@router.post("/templates/{template_id}/redraft")
async def redraft_template(template_id: int, payload: dict):
    instructions = (payload.get("instructions") or "").strip()
    if not instructions:
        return JSONResponse({"error": "instructions are required"}, status_code=400)
    row = await db.fetchone("SELECT * FROM email_templates WHERE id = ?", template_id)
    if not row:
        return JSONResponse({"error": "no such template"}, status_code=404)
    try:
        html = await _generate_html(
            f"{instructions}\n\nHere is the current template to improve "
            f"(keep its spirit, change what is asked):\n{row['html']}")
    except ValueError as exc:
        return JSONResponse({"error": f"the model produced no usable template: {exc}"},
                            status_code=502)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)[:300]}, status_code=503)
    # Keep the original untouched; save the rewrite as a new variant.
    name = f"{row['name']}-v2"
    n = 2
    while await db.fetchone("SELECT id FROM email_templates WHERE name = ?", name):
        n += 1
        name = f"{row['name']}-v{n}"
    await db.execute(
        "INSERT INTO email_templates (name, scope_email, html, description, created_by) "
        "VALUES (?, ?, ?, ?, 'agent')",
        (name, row["scope_email"], html, f"redraft of {row['name']}: {instructions[:200]}"))
    new_row = await db.fetchone("SELECT * FROM email_templates WHERE name = ?", name)
    return {"ok": True, "template": dict(new_row),
            "summary": f"created '{name}' from '{row['name']}'"}


def get_router() -> APIRouter:
    """Gateway contract: called at load and on hot-reload."""
    return router
