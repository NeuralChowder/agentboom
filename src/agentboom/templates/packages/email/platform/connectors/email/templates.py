"""Email template engine — the ONE place outgoing mail gets dressed.

Doctrine (shaped by the production agent this is extracted from):
- There is a single send path; it applies the template automatically, so
  every sender (replies, invoices, digests) gets a consistent look for
  free and nobody hand-builds wrappers.
- Zero setup: every mailbox uses the built-in default until a template
  is activated for it. Installing the `email-templates` package unlocks
  the library/activation UI; without it, the default still applies.
- The plain-text part is never touched, so the wrapper/footer is never
  quoted back into replies. Only the HTML part is wrapped.
- Cross-language callers (a Node skill, another mini-app) do NOT
  re-implement the renderer — they use the `email.render` capability
  exposed by the email-templates mini-app, which wraps `render()` here.

The custom-template tables are created by the `email-templates` package
migration; every lookup here degrades to the built-in default when they
are absent, so the engine is safe to call unconditionally.
"""
from __future__ import annotations

import html as _html
import logging
import os
from typing import Optional

from agentboom_sdk import db

log = logging.getLogger("connectors.email.templates")

# Footer is configurable so each agent can brand it (or blank it). The
# default is deliberately quiet.
DEFAULT_FOOTER = os.environ.get(
    "EMAIL_TEMPLATE_FOOTER",
    "Sent by an agentboom assistant")

# Set EMAIL_TEMPLATE_DISABLE=1 to send bare HTML with no wrapper at all.
DISABLED = os.environ.get("EMAIL_TEMPLATE_DISABLE", "").lower() in ("1", "true", "yes")

# Built-in default: a white card for the message on a soft background,
# with a small muted footer. Inline CSS only (email-safe).
DEFAULT_TEMPLATE = """<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background-color:#f4f5f7;">
  <div style="background-color:#f4f5f7;padding:24px 12px;">
    <div style="max-width:600px;margin:0 auto;background-color:#ffffff;
                border-radius:10px;padding:28px 32px;
                font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
                color:#22262b;font-size:15px;line-height:1.6;">
      {{body}}
    </div>
    <div style="max-width:600px;margin:14px auto 0;text-align:center;
                font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
                color:#9aa1ab;font-size:11px;line-height:1.5;">
      {{footer}}
    </div>
  </div>
</body>
</html>"""


def text_to_html(text: str) -> str:
    """Turn plain text into simple HTML paragraphs (escaped)."""
    escaped = _html.escape(text or "")
    parts = [p.strip() for p in escaped.split("\n\n") if p.strip()]
    if not parts:
        return ""
    return "\n".join(f"<p style=\"margin:0 0 12px;\">{p}</p>" for p in parts)


def wrap(body_html: str, template_html: str, footer: Optional[str] = None) -> str:
    """Inject the message body into a template's {{body}} slot."""
    out = template_html.replace("{{body}}", body_html or "")
    out = out.replace("{{footer}}", footer if footer is not None else DEFAULT_FOOTER)
    return out


async def active_template_html(account_email: str) -> str:
    """The template a mailbox sends with right now.

    Resolution: the mailbox's activated template -> the built-in default.
    Degrades to the default when the email-tables are absent (the
    email-templates package not installed) or the lookup fails.
    """
    if not account_email:
        return DEFAULT_TEMPLATE
    try:
        row = await db.fetchone(
            "SELECT t.html FROM email_template_active a "
            "JOIN email_templates t ON t.id = a.template_id "
            "WHERE a.account_email = ?",
            (account_email.strip().lower(),))
        if row and row.get("html"):
            return row["html"]
    except Exception:  # noqa: BLE001 — tables absent / lookup failed -> default
        pass
    return DEFAULT_TEMPLATE


async def render(body: str, account_email: str, html: Optional[str] = None) -> str:
    """Produce the final HTML part for an outgoing message.

    `body` is the plain-text message (always preserved as the text part).
    `html`, when given, is the caller's own HTML for the message body;
    otherwise simple HTML is derived from `body`. The result is wrapped
    in the mailbox's active template (or the built-in default).
    """
    if DISABLED:
        return html if html is not None else text_to_html(body)
    inner = html if html is not None else text_to_html(body)
    template = await active_template_html(account_email)
    return wrap(inner, template)
