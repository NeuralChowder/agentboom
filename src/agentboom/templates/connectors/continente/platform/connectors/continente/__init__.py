"""Continente connector — continente.pt (agentboom package: continente).

HTTP client for the Portuguese supermarket Continente: server-rendered
search tiles, product pages (PDP JSON-LD), order history, and the SFCC
cart (add + read).

Auth model — a session is a cookie jar, not a password
---------------------------------------------------------
Pure-HTTP login is blocked (the login.continente.pt IdP fingerprints
TLS/HTTP2 vs User-Agent and returns Code 100000 to non-browser clients),
so this connector NEVER logs in. The session is a flat {name: value}
cookie jar captured from the user's real browser (`document.cookie`),
stored in the vault (vault package) under service `continente.pt:cookies`
— AES-256-GCM with the VAULT_KEY, audit-logged on write, never in a file,
never returned by any mini-app route.

A `sid` cookie is NOT a login: SFCC issues anonymous carts sids too, and
cart adds on an anonymous session silently land in a throwaway guest
cart. Every cart mutation is therefore gated by the login probe —
GET /conta/moradas/ must return 200 AND render `data-address-id` (the
saved-address cards) — cached ~5 min process-wide.

Search note (2026-08-27 drift): `/pesquisa/?q=..` now returns the
homepage shell, byte-identical for every query. Live results moved to
the `Search-ShowAjax` action (same tile markup, plus
`X-Requested-With: XMLHttpRequest`) — that is what search() calls.

Mini-app / agent usage:

    from connectors.continente import search, product, cart_add

    results = await search("arroz", limit=5)
    pdp     = await product("6927230")
    added   = await cart_add("6927230", quantity=2)

Env:
  VAULT_KEY                vault master key (vault package) — needed to
                           read/write the stored session
  CONTINENTE_TIMEOUT_SEC   http timeout (default 20)
  CONTINENTE_PROBE_TTL_SEC login-probe cache TTL (default 300)
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
import time
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import httpx

from agentboom_sdk import db

log = logging.getLogger("connectors.continente")

BASE = "https://www.continente.pt"
#: SFCC action endpoint — cart / search XHRs.
ACTION = f"{BASE}/on/demandware.store/Sites-continente-Site/default"

#: Browser-like identity. The read/cart endpoints are plain (no anti-bot
#: tokens); only the IdP fingerprints, and we never call it.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS: Dict[str, str] = {"User-Agent": UA, "Accept-Language": "pt-PT,pt;q=0.9"}

_TIMEOUT = float(os.environ.get("CONTINENTE_TIMEOUT_SEC", "20"))
_PROBE_TTL_SEC = float(os.environ.get("CONTINENTE_PROBE_TTL_SEC", "300"))

# ── vault-backed session storage ────────────────────────────────────
# Vault naming convention is `<service>:<purpose>` — one credential per
# purpose. The jar is derived state (regenerable by re-login), but it is
# still a secret: it IS the account session.
VAULT_SERVICE = "continente.pt:cookies"
_VAULT_NOTE = "continente.pt session cookie jar (document.cookie)"
_KEY_LEN_BYTES = 32
_NONCE_LEN_BYTES = 12


class ContinenteError(RuntimeError):
    """A Continente call failed (site error, bad response, vault problem)."""


class SessionError(ContinenteError):
    """No usable session — caller maps to HTTP 503 with the message."""


# ── vault layer (same crypto as the vault mini-app) ─────────────────
# The vault package owns the tables and the key; we mirror its AES-256-GCM
# usage (per-secret nonce, AAD = service) so the entries are interchangeable
# with /api/vault. Write paths audit to vault_audit; reads are not audited —
# they are high-frequency (health probes) and run in-process, where the
# holder already has VAULT_KEY.


def _vault_key() -> Optional[bytes]:
    raw = os.environ.get("VAULT_KEY", "").strip()
    if not raw:
        return None
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        return None
    return key if len(key) == _KEY_LEN_BYTES else None


async def _vault_audit(action: str, detail: str = "") -> None:
    try:
        await db.execute(
            "INSERT INTO vault_audit (service, action, detail) VALUES (?, ?, ?)",
            (VAULT_SERVICE, action, detail),
        )
    except Exception:  # noqa: BLE001 — audit is best-effort, the op is not
        log.warning("continente: vault audit write failed (%s)", action)


async def _vault_get() -> Optional[str]:
    """The stored cookie-jar JSON string, or None."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _vault_key()
    if key is None:
        return None
    try:
        row = await db.fetchone(
            "SELECT encrypted, nonce FROM vault_credentials WHERE service = ?",
            (VAULT_SERVICE,),
        )
    except Exception:  # noqa: BLE001 — vault tables missing = no session
        log.warning("continente: cannot read vault (vault package applied?)")
        return None
    if row is None:
        return None
    try:
        return AESGCM(key).decrypt(bytes(row["nonce"]), bytes(row["encrypted"]),
                                   VAULT_SERVICE.encode("utf-8")).decode("utf-8")
    except Exception:  # noqa: BLE001 — wrong key / corruption: never leak detail
        log.error("continente: vault decrypt failed (wrong VAULT_KEY?)")
        return None


async def _vault_put(secret: str) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _vault_key()
    if key is None:
        raise ContinenteError(
            "vault disabled: VAULT_KEY not set or invalid "
            "(expected `openssl rand -hex 32`)")
    import secrets as _secrets
    nonce = _secrets.token_bytes(_NONCE_LEN_BYTES)
    encrypted = AESGCM(key).encrypt(nonce, secret.encode("utf-8"),
                                    VAULT_SERVICE.encode("utf-8"))
    await db.execute(
        """
        INSERT INTO vault_credentials (service, encrypted, nonce, note)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(service) DO UPDATE SET
            encrypted = excluded.encrypted,
            nonce = excluded.nonce,
            note = COALESCE(excluded.note, vault_credentials.note),
            updated_at = CURRENT_TIMESTAMP
        """,
        (VAULT_SERVICE, encrypted, nonce, _VAULT_NOTE),
    )
    await _vault_audit("store", f"{len(secret)} chars")


async def _vault_delete() -> bool:
    try:
        removed = await db.execute(
            "DELETE FROM vault_credentials WHERE service = ?", (VAULT_SERVICE,))
    except Exception:  # noqa: BLE001
        log.warning("continente: vault delete failed")
        return False
    if removed:
        await _vault_audit("delete")
    return bool(removed)


# ── session (vault-backed cookie jar) ───────────────────────────────


async def get_cookies() -> Dict[str, str]:
    """The stored cookie jar; {} when no session (or the vault is unusable)."""
    secret = await _vault_get()
    if secret is None:
        return {}
    try:
        data = json.loads(secret)
    except ValueError:
        log.error("continente: stored session is not valid JSON — treating as absent")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


async def set_cookies(cookies: dict) -> int:
    """Store the cookie jar in the vault. Returns the number of cookies."""
    if not isinstance(cookies, dict) or not cookies:
        raise ContinenteError("cookies must be a non-empty {name: value} dict")
    clean = {str(k): str(v) for k, v in cookies.items()}
    await _vault_put(json.dumps(clean, ensure_ascii=False))
    return len(clean)


async def clear_session() -> bool:
    """Delete the stored session. Returns True when a row was removed."""
    return await _vault_delete()


def parse_cookie_string(text: str) -> Dict[str, str]:
    """Parse a `document.cookie`-style "a=b; c=d" string.

    Splits on ';', strips each pair, skips empties and pairs without '='.
    Values may themselves contain '=' (split on the first '=' only).
    """
    out: Dict[str, str] = {}
    for part in (text or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if not name:
            continue
        out[name] = value.strip()
    return out


# ── login probe ─────────────────────────────────────────────────────
# A `sid` is not a login (anonymous carts have sids). The discriminator
# is the saved-addresses page: a logged-in session renders
# `[data-address-id]` cards; an anonymous one does not. Probe failure
# means REFUSE the mutation, never allow it.


_PROBE_CACHE: Dict[str, Any] = {"at": 0.0, "value": False, "reason": ""}


async def is_logged_in(cookies: Optional[Dict[str, str]] = None,
                       max_age: Optional[float] = None,
                       force: bool = False) -> Tuple[bool, str]:
    """(logged_in, reason). Cookies default to the stored vault session.

    No cookies at all → (False, "no session stored") without any HTTP.
    The HTTP result is cached module-level for ~5 min (max_age / force
    override it) so a burst of adds costs one probe.
    """
    if cookies is None:
        cookies = await get_cookies()
    if not cookies:
        return False, "no session stored"
    ttl = _PROBE_TTL_SEC if max_age is None else float(max_age)
    now = time.monotonic()
    if not force and now - _PROBE_CACHE["at"] < ttl:
        return bool(_PROBE_CACHE["value"]), str(_PROBE_CACHE["reason"])
    try:
        status, _, raw = await http_get(f"{BASE}/conta/moradas/", cookies=cookies)
        if status == 200 and b"data-address-id" in raw:
            value, reason = True, "logged in (saved-addresses page rendered)"
        elif status == 200:
            value, reason = False, ("session present but not logged in "
                                    "(anonymous or expired)")
        else:
            value, reason = False, f"probe failed (HTTP {status})"
    except Exception as exc:  # noqa: BLE001 — probe failure = refuse, not allow
        value, reason = False, f"probe failed: {exc}"
    _PROBE_CACHE.update(at=now, value=value, reason=reason)
    return value, reason


# ── http seams (tests monkeypatch these two) ────────────────────────


async def http_get(url: str, headers: Optional[dict] = None,
                   cookies: Optional[dict] = None) -> Tuple[int, dict, bytes]:
    """Outbound GET seam. Returns (status, headers, body); transport
    failure → (0, {}, reason-bytes)."""
    return await _http("GET", url, headers=headers, data=None, cookies=cookies)


async def http_post(url: str, headers: Optional[dict] = None,
                    data: Optional[dict] = None,
                    cookies: Optional[dict] = None) -> Tuple[int, dict, bytes]:
    """Outbound POST seam (form-encoded when `data` is a dict)."""
    return await _http("POST", url, headers=headers, data=data, cookies=cookies)


async def _http(method: str, url: str, headers: Optional[dict] = None,
                data: Optional[dict] = None,
                cookies: Optional[dict] = None) -> Tuple[int, dict, bytes]:
    import urllib.parse

    hdrs = dict(HEADERS)
    if headers:
        hdrs.update(headers)
    kwargs: Dict[str, Any] = {}
    if data is not None:
        kwargs["data"] = urllib.parse.urlencode(data)
        hdrs.setdefault("Content-Type",
                        "application/x-www-form-urlencoded; charset=UTF-8")
    if cookies:
        hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=hdrs, **kwargs)
        return resp.status_code, dict(resp.headers), resp.content
    except httpx.HTTPError as exc:
        log.warning("continente: %s %s failed: %s", method, url, exc)
        return 0, {}, str(exc).encode("utf-8", "replace")


# ── catalog (no auth needed) ────────────────────────────────────────

#: Product tile: outer div.product[data-pid]; the inner div.product-tile
#: repeats the pid and is skipped by the exact-`product` class check.
_TILE_RE = re.compile(r'<div([^>]*)data-pid="(\d+)"([^>]*)>')
_HREF_RE = re.compile(r'href=["\'](?:https://www\.continente\.pt)?(/produto/[^"\'?]+)')


def _num(value: Any) -> Optional[float]:
    """'1,89€' / '1.89' / 1.89 / None -> 1.89 (or None)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    m = re.search(r"(\d+(?:[.,]\d{1,2})?)", str(value))
    if not m:
        return None
    try:
        return round(float(m.group(1).replace(",", ".")), 2)
    except ValueError:
        return None


def _price_value(value: Any) -> Optional[float]:
    """Cart price shapes: number, '0,94€', or {"sales": {"value": 0.94}}."""
    if isinstance(value, dict):
        sales = value.get("sales") or {}
        value = sales.get("value", value.get("value"))
    return _num(value)


def parse_tiles(page: str, limit: int = 20) -> List[dict]:
    """Search-result tiles → [{pid, title, image, price, url}].

    The stable parse target is `data-product-tile-impression` — an
    HTML-escaped JSON attribute {name, id, price, brand}; the formatted
    markup is only a fallback. PDP hrefs sit ~4000+ chars into the tile,
    so each tile is scanned up to 8000 chars ahead. Deduped by pid.
    """
    results: List[dict] = []
    seen: set = set()
    for m in _TILE_RE.finditer(page):
        pid = m.group(2)
        if pid in seen:
            continue
        tag_attrs = m.group(1) + m.group(3)
        class_m = re.search(r'class="([^"]*)"', tag_attrs)
        if not class_m or "product" not in class_m.group(1).split():
            continue  # inner product-tile / quantity divs repeat the pid
        block = page[m.end():m.end() + 8000]
        title = price = brand = None
        imp = re.search(r"data-product-tile-impression='([^']*)'", block)
        if imp:
            try:
                data = json.loads(_html.unescape(imp.group(1)))
                title = data.get("name")
                price = _num(data.get("price"))
                brand = data.get("brand") or None
            except ValueError:
                pass
        url_m = _HREF_RE.search(block)
        image_m = re.search(r'<img[^>]+src="([^"]+)"', block)
        results.append({
            "pid": pid,
            "title": title,
            "image": image_m.group(1) if image_m else None,
            "price": price,
            "brand": brand,
            "url": BASE + url_m.group(1) if url_m else None,
        })
        seen.add(pid)
        if len(results) >= limit:
            break
    return results


async def search(query: str, start: int = 0, limit: int = 20) -> List[dict]:
    """Search via the `Search-ShowAjax` action.

    `GET {ACTION}/Search-ShowAjax?q=<term>` with
    `X-Requested-With: XMLHttpRequest`. (The old `/pesquisa/` page is
    dead — it returns the homepage shell for every query, so parsing it
    silently yields the same ~10 unrelated products.)
    """
    query = (query or "").strip()
    if not query:
        raise ContinenteError("query is required")
    limit = max(1, min(int(limit), 50))
    import urllib.parse
    params = urllib.parse.urlencode({"q": query})
    if start:
        params += f"&start={int(start)}"
    status, _, raw = await http_get(
        f"{ACTION}/Search-ShowAjax?{params}",
        headers={"X-Requested-With": "XMLHttpRequest"})
    if status != 200:
        raise ContinenteError(f"search failed (HTTP {status})")
    return parse_tiles(raw.decode("utf-8", "replace"), limit=limit)


async def product(pid: Any) -> Optional[dict]:
    """One product page (PDP) — `GET /produto/p-<pid>.html`.

    Returns {pid, title, image, price, price_valid_until, availability,
    in_stock} with None-safe fields, or None when the page is missing.
    Price/validity/stock come from the JSON-LD Offer (the reliable
    signal); og:title/og:image carry the display fields.
    """
    pid = str(pid or "").strip()
    if not pid:
        return None
    status, _, raw = await http_get(f"{BASE}/produto/p-{pid}.html")
    if status != 200:
        return None
    page = raw.decode("utf-8", "replace")
    out: Dict[str, Any] = {
        "pid": pid, "title": None, "image": None, "price": None,
        "price_valid_until": None, "availability": None, "in_stock": None,
    }
    title_m = re.search(r'<meta[^>]+property="og:title"\s+content="([^"]+)"', page)
    if title_m:
        title = _html.unescape(title_m.group(1))
        title = re.sub(r"\s*\|\s*Continente Online\s*$", "", title).strip()
        out["title"] = title or None
    img_m = (re.search(r'<meta[^>]+property="og:image"\s+content="([^"]+)"', page)
             or re.search(r'"image"\s*:\s*\["([^"]+)"', page))
    if img_m:
        out["image"] = _html.unescape(img_m.group(1))
    offer_m = re.search(r'"@type"\s*:\s*"Offer".{0,600}', page, re.S)
    if offer_m:
        block = offer_m.group(0)
        p = re.search(r'"price"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)"?', block)
        if p:
            out["price"] = _num(p.group(1))
        v = re.search(r'"priceValidUntil"\s*:\s*"(\d{4}-\d{2}-\d{2})"', block)
        if v:
            out["price_valid_until"] = v.group(1)
    avail = re.search(r'"availability"\s*:\s*"http://schema.org/(\w+)"', page)
    if avail:
        out["availability"] = avail.group(1)
        out["in_stock"] = avail.group(1) in (
            "InStock", "LimitedAvailability", "PreOrder", "InStoreOnly")
    return out


# ── orders (session) ────────────────────────────────────────────────

# PT + EN month abbreviations — the account page locale is not something
# to bet on, so the parser is locale-safe instead of using strptime %b.
_MONTHS: Dict[str, int] = {
    "jan": 1, "fev": 2, "feb": 2, "mar": 3, "abr": 4, "apr": 4,
    "mai": 5, "may": 5, "jun": 6, "jul": 7, "ago": 8, "aug": 8,
    "set": 9, "sep": 9, "out": 10, "oct": 10, "nov": 11, "dez": 12,
    "dec": 12,
}
_ORDER_DATE_RE = re.compile(r"\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4})\s*")


def parse_order_date(text: Optional[str]) -> Optional[str]:
    """'17 Ago 25' / '17 Aug 25' → 'YYYY-MM-DD', or None.

    The month must be exactly the 3-letter PT/EN abbreviation (the
    strptime-%b contract, made locale-safe by the explicit map) —
    longer words like 'Augosto' do not parse."""
    m = _ORDER_DATE_RE.fullmatch(text or "")
    if not m:
        return None
    day = int(m.group(1))
    month = _MONTHS.get(m.group(2)[:3].lower())
    year = int(m.group(3))
    if month is None:
        return None
    if year < 100:  # '25' means 2025
        year += 2000
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _order_lines(section: str) -> List[dict]:
    """Product list of one detail page → line items."""
    lines: List[dict] = []
    for chunk in section.split("ct-order-history--product-item")[1:]:
        pid_m = re.search(r'data-pid="(\d+)"', chunk)
        name_m = re.search(r'ct-order-history--product-title[^>]*>([^<]+)<', chunk)
        if not (pid_m and name_m):
            continue
        brand_m = re.search(r'ct-order-history--product-brand[^>]*>([^<]+)<', chunk)
        cat_m = re.search(r'data-category="([^"]*)"', chunk)
        qty_m = re.search(r'ct-order-history--product-quantity">\s*(\d+)\s*un', chunk)
        total_m = re.search(
            r'ct-order-history--product-total-price">\s*(\d+[.,]\d{2})\s*€', chunk)
        lines.append({
            "pid": pid_m.group(1),
            "name": _html.unescape(name_m.group(1)).strip(),
            "brand": _html.unescape(brand_m.group(1)).strip() if brand_m else None,
            "category": _html.unescape(cat_m.group(1)).strip() if cat_m else None,
            "quantity": int(qty_m.group(1)) if qty_m else 1,
            "total_eur": _num(total_m.group(1)) if total_m else None,
        })
    return lines


def _order_total(page: str) -> Optional[float]:
    """Best-effort order total (best effort: the detail page has no single
    documented total marker — JSON-LD Order first, then the SFCC
    order-total classes, then the PT 'Total' label)."""
    m = re.search(r'"@type"\s*:\s*"Order".{0,2000}?'
                  r'"totalPrice"\s*:\s*"?([0-9]+(?:[.,][0-9]+)?)', page, re.S)
    if m:
        return _num(m.group(1))
    m = re.search(r'[^"\w]order-total[^>]*>\s*(?:<[^>]+>\s*)*'
                  r'(\d+[.,]\d{2})\s*€', page)
    if m:
        return _num(m.group(1))
    m = re.search(r'>\s*Total\b[^<]{0,40}?</[^>]+>\s*(\d+[.,]\d{2})\s*€', page)
    if m:
        return _num(m.group(1))
    return None


async def order_detail(order_id: str,
                       cookies: Optional[Dict[str, str]] = None) -> dict:
    """One order (session) → {order_id, total_eur, lines: [...]}.

    Raises SessionError when there is no session or the site does not
    accept the cookies (expired → the site answers non-200).
    """
    if cookies is None:
        cookies = await get_cookies()
    if not cookies:
        raise SessionError("no Continente session stored — "
                           "ask the agent 'set up continente'")
    status, _, raw = await http_get(
        f"{BASE}/conta/detalhe-encomenda/?orderID={order_id}", cookies=cookies)
    if status != 200:
        raise SessionError(
            f"order {order_id} unreadable (HTTP {status}) — "
            "session missing or expired")
    page = raw.decode("utf-8", "replace")
    start = page.find("ct-order-history--products-list")
    section = page[start:start + 300_000] if start != -1 else page
    return {"order_id": order_id, "total_eur": _order_total(page),
            "lines": _order_lines(section)}


async def orders(limit: int = 6,
                 cookies: Optional[Dict[str, str]] = None) -> List[dict]:
    """Recent orders (session) → [{order_id, date, total_eur, lines}].

    The list page yields order ids + delivery dates; each order's lines
    come from its detail page (bounded by limit).
    """
    limit = max(1, min(int(limit), 20))
    if cookies is None:
        cookies = await get_cookies()
    if not cookies:
        raise SessionError("no Continente session stored — "
                           "ask the agent 'set up continente'")
    status, _, raw = await http_get(f"{BASE}/conta/encomendas/", cookies=cookies)
    if status != 200:
        raise SessionError(
            f"order history unreadable (HTTP {status}) — "
            "session missing or expired")
    page = raw.decode("utf-8", "replace")
    out: List[dict] = []
    # One order = delivery-date div, then the detail link; the date lives
    # in the div *before* the link, so read backwards from the link.
    for id_m in re.finditer(r'detalhe-encomenda/\?orderID=([0-9a-f-]+)', page):
        window = page[max(0, id_m.start() - 3000):id_m.start()]
        # Use the LAST date div before the link (re.search returns the
        # first — if two blocks fit in the window the second order would
        # steal the first order's date).
        _all_dates = re.findall(
            r'ct-order-history--order-preview-delivery-date">\s*([^<]+?)\s*<',
            window)
        order_id = id_m.group(1)
        detail = await order_detail(order_id, cookies=cookies)
        out.append({
            "order_id": order_id,
            "date": parse_order_date(_all_dates[-1].strip()) if _all_dates else None,
            "total_eur": detail["total_eur"],
            "lines": detail["lines"],
        })
        if len(out) >= limit:
            break
    return out


# ── cart (session, login-gated) ─────────────────────────────────────


async def cart(cookies: Optional[Dict[str, str]] = None) -> dict:
    """Current cart (session) → {total_eur, num_items, items: [...]}.

    Items: {pid, name, quantity, price_eur, line_total_eur}. Raises
    SessionError when there is no session / it expired.
    """
    if cookies is None:
        cookies = await get_cookies()
    if not cookies:
        raise SessionError("no Continente session stored — "
                           "ask the agent 'set up continente'")
    status, _, raw = await http_get(
        f"{ACTION}/Cart-Get",
        headers={"Accept": "application/json"}, cookies=cookies)
    if status in (401, 403):
        raise SessionError("session expired — re-run setup")
    if status != 200:
        raise ContinenteError(f"Cart-Get failed (HTTP {status})")
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ContinenteError("Cart-Get returned unreadable JSON")
    items = []
    for item in data.get("items") or []:
        items.append({
            "pid": str(item.get("id")),
            "name": item.get("productName"),
            "quantity": item.get("quantity"),
            "price_eur": _price_value(item.get("price")),
            "line_total_eur": _price_value(item.get("priceTotal")),
        })
    totals = data.get("totals") or {}
    total = totals.get("grandTotalNumber")
    total = _num(total) if isinstance(total, (int, float)) \
        else _num(totals.get("grandTotal"))
    return {"total_eur": total, "num_items": data.get("numItems"),
            "items": items}


async def cart_add(pid: Any, quantity: int = 1,
                   cookies: Optional[Dict[str, str]] = None) -> dict:
    """Add one product to the account cart (session, login-gated).

    Refuses with SessionError when there is no session or the moradas
    probe fails — an anonymous add would silently land in a throwaway
    guest cart. Returns {ok, pid, quantity, message, cart_total_eur}.
    """
    pid = str(pid or "").strip()
    if not pid:
        raise ContinenteError("pid is required")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ContinenteError("quantity must be an integer")
    if not 1 <= quantity <= 50:
        raise ContinenteError("quantity must be 1-50")
    if cookies is None:
        cookies = await get_cookies()
    if not cookies:
        raise SessionError("no Continente session stored — "
                           "ask the agent 'set up continente'")
    logged, reason = await is_logged_in(cookies)
    if not logged:
        raise SessionError(f"cart add refused — {reason}; anonymous adds land "
                           "in a throwaway guest cart, so they are never allowed")
    status, _, raw = await http_post(
        f"{ACTION}/Cart-AddProduct",
        headers={"Accept": "application/json"},
        data={"pid": pid, "quantity": str(quantity)}, cookies=cookies)
    if status in (401, 403):
        raise SessionError("session expired — re-run setup")
    if status != 200:
        raise ContinenteError(f"site refused the add (HTTP {status})")
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise ContinenteError("site returned an unreadable response")
    if data.get("error") or data.get("isProductInStock") is False:
        raise ContinenteError(
            data.get("message") or "product unavailable (out of stock)")
    state = await cart(cookies=cookies)
    return {"ok": True, "pid": pid, "quantity": quantity,
            "message": data.get("message"),
            "cart_total_eur": state["total_eur"]}
