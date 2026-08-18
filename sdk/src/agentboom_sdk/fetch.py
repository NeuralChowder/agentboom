"""Fetching a link that arrived in someone else's message.

A message says "your invoice is at https://...". Reading it would be useful.
The problem is the obvious one: the agent cannot tell from a URL whether
following it is safe, and neither can a model. "Does this look dangerous?" is
not a question with a reliable answer — a phishing link and a real invoice
link are the same shape.

So none of the guards here are judgement calls. They are structural, and they
hold whatever the model thinks:

  * **Public addresses only.** Every resolved IP must be globally routable.
    This blocks the confused-deputy attack that matters most here: a message
    containing `http://endpoint-platform:8000/api/vault/list` would otherwise
    make the agent read the principal's credentials on the sender's behalf.
    Cloud metadata (169.254.169.254), loopback, private ranges and bare
    service names are all refused. Resolution happens *before* connecting and
    the connection is pinned to the address that was checked, so a name that
    answers publicly once and privately the second time cannot slip through.
  * **GET only**, and redirects are followed by hand so each hop is
    re-checked. A redirect to an internal address is the standard way around
    a naive check.
  * **Never action-shaped.** `unsubscribe`, `confirm`, `verify`, `pay`,
    `cancel`, `delete`, `optout` — fetching those *performs* them. A GET is
    only safe if the far end agrees it is, and these say it is not.
  * **Sender-anchored.** The link's registrable domain must match the
    sender's, or be a host the caller explicitly allows. A stranger's link is
    not fetched.
  * **The result is untrusted.** Wrapped before it can reach a model, exactly
    like a message body — a fetched page is no more trustworthy than the
    message that linked to it.

One cost that is not technical: fetching a link in a phishing message
confirms the address is live. That is why this never runs by default — a rule
has to ask.

    from agentboom_sdk.fetch import fetch_untrusted_url

    page = await fetch_untrusted_url(url, sender_domain="supplier.example")
    if page.ok:
        ...  # page.text is already fenced
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from typing import Optional, Sequence
from urllib.parse import urlparse, urlunparse

import httpx

from agentboom_sdk.untrusted import wrap

log = logging.getLogger("agentboom_sdk.fetch")

MAX_BYTES = 2_000_000       # a document, not a download
MAX_REDIRECTS = 3
TIMEOUT_SEC = 20

USER_AGENT = "agentboom-fetch/1.0 (+automated; document fetch only)"

# Paths and query keys that *do* something when requested. Not a phishing
# heuristic — a list of words that mean "this GET has a side effect", where
# fetching is wrong even for a completely genuine sender.
_ACTION_WORDS = re.compile(
    r"(unsubscribe|opt[-_]?out|confirm|verify|activate|validate|"
    r"reset|revoke|cancel|delete|remove|approve|accept|decline|"
    r"pay|checkout|purchase|subscribe|signup|register|login|logout|"
    r"auth|token|session|invite|rsvp)",
    re.I,
)

_READABLE_TYPES = ("text/html", "text/plain", "application/pdf",
                   "application/xhtml+xml", "text/markdown")


#: Types `fetch_untrusted_bytes` will keep. A document, a scan, a photo of a
#: receipt. Nothing archive-shaped and nothing that any program interprets.
DOCUMENT_TYPES = (
    "application/pdf",
    "image/png", "image/jpeg", "image/jpg", "image/tiff", "image/webp",
    "image/heic", "image/heif",
    "application/msword",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.oasis.opendocument",
    "application/vnd.ms-excel",
)

#: What a link has to *look* like before we will pull bytes down it. An extra
#: condition on top of every check `_vet` makes, never a way round one.
_DOCUMENT_HINT = re.compile(
    r"(\.pdf|\.png|\.jpe?g|\.tiff?|\.docx?|\.xlsx?|"
    r"fatura|factura|invoice|recibo|receipt|nota|document|anexo|attach|"
    r"download|ficheiro|file)",
    re.I,
)

#: One downloaded document. 25MB is a generous scan; beyond that it is not an
#: invoice and we are being fed something.
MAX_DOCUMENT_BYTES = 25_000_000


@dataclass
class FetchResult:
    ok: bool
    url: str
    reason: str = ""
    text: str = ""
    content_type: str = ""


@dataclass
class BytesResult:
    """A document pulled from a link in someone else's message."""
    ok: bool
    url: str
    reason: str = ""
    content: bytes = b""
    content_type: str = ""
    filename: str = ""


def _registrable(host: str) -> str:
    """Last two labels of a hostname — good enough to compare sender to link.

    Deliberately crude. Erring toward narrower matching would reject genuine
    mail; erring wider only ever admits a domain the sender already controls a
    sibling of.
    """
    parts = (host or "").lower().strip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def _resolve_public_ips(host: str):
    """Resolve a hostname, refusing anything not globally routable.

    Returns (addresses, reason). A non-empty reason means: do not connect.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        return [], f"could not resolve {host}: {exc}"

    addresses = []
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return [], f"unparseable address for {host}"

        # is_global is the whole check: it already excludes loopback, private
        # ranges, link-local (and so 169.254.169.254), multicast and reserved
        # space, for both IPv4 and IPv6.
        if not ip.is_global:
            return [], f"{host} resolves to non-public address {ip}"
        addresses.append(raw)

    if not addresses:
        return [], f"no addresses for {host}"
    return addresses, ""


def _vet(url: str, allowed_domains: Sequence[str]):
    """Check one URL. Returns (host, reason); a reason means refuse."""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return None, f"scheme {parsed.scheme or '(none)'} is not fetchable"
    if not parsed.hostname:
        return None, "no host in URL"
    # Credentials in a URL are a redirect-parsing trick more often than a real
    # login, and nothing legitimate needs them here.
    if parsed.username or parsed.password:
        return None, "URL carries credentials"

    target = f"{parsed.path}?{parsed.query}"
    if _ACTION_WORDS.search(target):
        return None, "link looks like an action, not a document"

    if allowed_domains:
        host_domain = _registrable(parsed.hostname)
        if host_domain not in {_registrable(d) for d in allowed_domains}:
            return None, (f"{parsed.hostname} is not the sender's domain "
                          f"({', '.join(allowed_domains)})")

    return parsed.hostname, ""


@dataclass
class _Raw:
    """What came back, before anyone decided what to do with it."""
    ok: bool
    url: str
    reason: str = ""
    body: bytes = b""
    content_type: str = ""
    host: str = ""
    disposition: str = ""


async def _get_vetted(url: str, allowed: Sequence[str], *, accept: str) -> _Raw:
    """The one place a request leaves this process for a URL someone else chose.

    Every refusal lives here so that a second caller cannot accidentally get a
    weaker version of it.
    """
    seen = set()
    current = url

    for hop in range(MAX_REDIRECTS + 1):
        if current in seen:
            return _Raw(False, url, "redirect loop")
        seen.add(current)

        host, reason = _vet(current, allowed)
        if reason:
            log.info("Refused %s: %s", current[:120], reason)
            return _Raw(False, current, reason)

        addresses, reason = await asyncio.to_thread(_resolve_public_ips, host)
        if reason:
            log.warning("Refused %s: %s", current[:120], reason)
            return _Raw(False, current, reason)

        parsed = urlparse(current)
        # Connect to the address that was just vetted, not to the name.
        # Between the check and the connection a name can start answering with
        # a private address — the classic DNS-rebinding move.
        pinned = urlunparse(parsed._replace(netloc=_pin(parsed, addresses[0])))

        try:
            async with httpx.AsyncClient(
                timeout=TIMEOUT_SEC, follow_redirects=False, verify=True,
            ) as client:
                resp = await client.get(
                    pinned,
                    # The URL now carries a literal IP, so TLS would offer the
                    # IP as the server name and every certificate check would
                    # fail. `sni_hostname` sends the real name in the handshake
                    # while the socket still goes to the vetted address.
                    extensions={"sni_hostname": parsed.hostname},
                    headers={
                        "Host": parsed.netloc,
                        "User-Agent": USER_AGENT,
                        "Accept": accept,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            return _Raw(False, current, f"fetch failed: {exc}")

        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location")
            if not location:
                return _Raw(False, current, "redirect without a target")
            if hop == MAX_REDIRECTS:
                return _Raw(False, current, "too many redirects")
            # Re-vetted from the top of the loop — a redirect into private
            # space is the usual way past a check that only looks at the URL
            # someone typed.
            current = httpx.URL(current).join(location).__str__()
            continue

        if resp.status_code >= 400:
            return _Raw(False, current, f"HTTP {resp.status_code}")

        return _Raw(
            ok=True,
            url=current,
            body=resp.content,
            content_type=(resp.headers.get("content-type") or "").split(";")[0].strip(),
            host=host,
            disposition=resp.headers.get("content-disposition") or "",
        )

    return _Raw(False, url, "too many redirects")


async def fetch_untrusted_url(
    url: str,
    *,
    sender_domain: str = "",
    allow_domains: Sequence[str] = (),
    max_chars: int = 8000,
) -> FetchResult:
    """Fetch a link from an untrusted message, or explain why it was refused.

    `sender_domain` anchors the link to whoever sent it. Pass `allow_domains`
    to permit a known host that mails from elsewhere. Passing neither fetches
    from any public host, which should be a deliberate choice, not a default.
    """
    allowed = [d for d in (*allow_domains, sender_domain) if d]
    raw = await _get_vetted(
        url, allowed, accept="text/html,text/plain,application/pdf;q=0.9")
    if not raw.ok:
        return FetchResult(False, raw.url, raw.reason)

    if raw.content_type and not any(
            raw.content_type.startswith(t) for t in _READABLE_TYPES):
        return FetchResult(False, raw.url,
                           f"not a readable document ({raw.content_type})")

    text = _to_text(raw.body[:MAX_BYTES], raw.content_type)
    if not text.strip():
        return FetchResult(False, raw.url, "nothing readable at that address")

    return FetchResult(
        ok=True,
        url=raw.url,
        text=wrap(text[:max_chars], source=f"web:{raw.host}", kind="fetched page"),
        content_type=raw.content_type,
    )


async def fetch_untrusted_bytes(
    url: str,
    *,
    sender_domain: str = "",
    allow_domains: Sequence[str] = (),
    max_bytes: int = MAX_DOCUMENT_BYTES,
    allowed_types: Sequence[str] = DOCUMENT_TYPES,
) -> BytesResult:
    """Download the *document* behind a link, when a message says there is one.

    Stricter than `fetch_untrusted_url`, never looser: the link must look like
    a document, the response must *be* a document type (an HTML page at a
    `.pdf` URL is a login wall or a trap), and the size cap is enforced on the
    body actually received. The bytes are never executed, sniffed or rendered.
    """
    parsed = urlparse(url)
    if not _DOCUMENT_HINT.search(f"{parsed.path}?{parsed.query}"):
        return BytesResult(False, url, "link does not name a document")

    allowed = [d for d in (*allow_domains, sender_domain) if d]
    raw = await _get_vetted(
        url, allowed, accept="application/pdf,image/*,application/octet-stream;q=0.8")
    if not raw.ok:
        return BytesResult(False, raw.url, raw.reason)

    if not raw.content_type:
        return BytesResult(False, raw.url, "server did not say what it was sending")
    if not any(raw.content_type.startswith(t) for t in allowed_types):
        return BytesResult(False, raw.url,
                           f"{raw.content_type} is not a document type")
    if not raw.body:
        return BytesResult(False, raw.url, "empty response")
    if len(raw.body) > max_bytes:
        return BytesResult(False, raw.url,
                           f"{len(raw.body)} bytes is over the {max_bytes} byte limit")

    log.info("Downloaded %s (%s, %d bytes)",
             raw.url[:120], raw.content_type, len(raw.body))
    return BytesResult(
        ok=True,
        url=raw.url,
        content=raw.body,
        content_type=raw.content_type,
        filename=_filename_for(raw),
    )


def _filename_for(raw: _Raw) -> str:
    """A name for a downloaded document: what the server called it, else the
    last path segment, else the content type."""
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', raw.disposition, re.I)
    if match:
        return match.group(1).strip()

    tail = urlparse(raw.url).path.rsplit("/", 1)[-1]
    if tail and "." in tail:
        return tail

    extension = {
        "application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg",
        "image/tiff": "tiff", "image/webp": "webp",
    }.get(raw.content_type, "bin")
    return f"document.{extension}"


def _pin(parsed, address: str) -> str:
    """Rebuild netloc against a literal IP, keeping the port."""
    literal = f"[{address}]" if ":" in address else address
    return f"{literal}:{parsed.port}" if parsed.port else literal


def _to_text(body: bytes, content_type: str) -> str:
    """Readable text from a fetched body. HTML is stripped, never rendered."""
    if content_type == "application/pdf":
        # PDFs go to an extraction service; callers that want one should pass
        # it there rather than have two decoders drift apart.
        return ""

    text = body.decode("utf-8", errors="replace")
    if "html" in content_type:
        text = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;?", " ", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()
