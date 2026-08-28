"""Sender verification — is this mail really from who it claims to be?

The `From` header is the one part of an email that anyone on the internet
can write. Every action that is keyed on "who sent this" — a verification
code, a bank notice, a contact change, a payment instruction — therefore has
to rest on what the *network* said, not on what the message says. This module
is the one place in the platform that knows how to read that evidence:

* the ``Authentication-Results`` header — the receiving MX's own verdict on
  SPF / DKIM / DMARC. The ingester stores it at receive time in
  ``email_cache.metadata->'sender_auth'`` (see
  ``services/email_gateway.py``), so every consumer reads the same stored
  verdict instead of re-deriving it.
* the shape of the sender domain — lookalike detection (typo domains,
  homoglyphs, TLD swaps) that works even when no auth data exists.

Everything here is pure: no IO, no network, no configuration. Callers pass
in what they have and get a verdict back. A missing signal is reported as
``unverified`` — never silently upgraded to ``ok``, because the whole point
is that "we could not check" and "it checked out" are different answers.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

__all__ = [
    "domain_of",
    "normalize_domain",
    "is_lookalike_domain",
    "parse_auth_results",
    "auth_status",
    "build_sender_auth",
    "verdict",
]

_ADDR_RE = re.compile(r"<([^<>]+)>")
_MECH_RE = re.compile(r"\b(spf|dkim|dmarc)\s*=\s*([a-z]+)", re.IGNORECASE)


def domain_of(address: Optional[str]) -> str:
    """The lowercased domain of ``Name <a@b.pt>`` or ``a@b.pt``; '' if none.

    The display name is deliberately ignored — it is free text and the one
    part of the address a forger controls completely.
    """
    if not address:
        return ""
    match = _ADDR_RE.search(address)
    candidate = match.group(1) if match else address.split()[-1]
    return candidate.rsplit("@", 1)[-1].strip().lower().strip(".")


def normalize_domain(domain: str) -> str:
    """NFKC + lowercase. Catches the Unicode homoglyph impostor
    (``seɡ-ѕocial.pt`` reduces to the string it imitates); ASCII confusables
    are handled by :func:`is_lookalike_domain`, not by normalization."""
    return unicodedata.normalize("NFKC", domain or "").lower().strip(".")


def is_lookalike_domain(candidate: str, expected: str) -> bool:
    """True when ``candidate`` is close enough to ``expected`` to be an
    impostor. Conservative on purpose, and it is a *signal*, not a verdict —
    the caller combines it with the auth data:

    * identical after normalization  -> not a lookalike (it *is* the domain);
    * a single-character substitution in domains of 6+ chars (typo squat:
      ``example-bank.pt`` -> ``example-bank.0pt``);
    * the same domain labels with a different TLD (``company.pt`` vs
      ``company.com``).
    """
    a, b = normalize_domain(candidate), normalize_domain(expected)
    if not a or not b or a == b:
        return False
    if len(a) >= 6 and len(a) == len(b):
        if sum(1 for x, y in zip(a, b) if x != y) == 1:
            return True
    a_labels, b_labels = a.split("."), b.split(".")
    if len(a_labels) >= 2 and a_labels[:-1] == b_labels[:-1] \
            and a_labels[-1] != b_labels[-1]:
        return True
    return False


def parse_auth_results(*values: Optional[str]) -> Dict[str, List[str]]:
    """Parse one or more ``Authentication-Results`` lines into every verdict
    that hop recorded. A message may carry several lines (each relay that
    checks appends its own), and the hops disagree — an upstream relay says
    ``spf=none`` about its own segment while the receiving MX says
    ``spf=pass`` about the path that matters. So every occurrence is kept,
    in line order (the receiving MX's own line comes first, which is why the
    pass rules below look at the first entry), and :func:`auth_status` does
    the weighing. Absent mechanisms stay empty: unknown is not fail, and
    guessing one from the other would be inventing evidence."""
    out: Dict[str, List[str]] = {"spf": [], "dkim": [], "dmarc": []}
    for value in values:
        if not value:
            continue
        for m in _MECH_RE.finditer(value):
            out[m.group(1).lower()].append(m.group(2).lower())
    return out


def auth_status(auth: Optional[Dict[str, Any]]) -> str:
    """Collapse the evidence into one status. ``auth`` holds each mechanism
    as a list of recorded verdicts (or a single string, for callers that
    have only one line):

    * ``fail``       any hop recorded an explicit failure — a failure at any
                     point on the path is a contradiction in the network's
                     own record;
    * ``pass``       DMARC passed (only the final receiver evaluates it, and
                     it is precisely the question "is the From domain
                     real?", aligned with SPF/DKIM), or the receiving MX —
                     the first recorded line — passed both SPF and DKIM;
    * ``unverified`` no data, or only weak/partial signals (SPF alone,
                     softfail, errors). Deliberately never ``pass``.
    """
    if not auth:
        return "unverified"

    def _vals(key: str) -> List[str]:
        v = auth.get(key)
        if not v:
            return []
        return [v] if isinstance(v, str) else list(v)

    all_vals = [v for k in ("spf", "dkim", "dmarc") for v in _vals(k)]
    if not all_vals:
        return "unverified"
    if any(v == "fail" for v in all_vals):
        return "fail"
    if "pass" in _vals("dmarc"):
        return "pass"
    spf, dkim = _vals("spf"), _vals("dkim")
    if spf and dkim and spf[0] == "pass" and dkim[0] == "pass":
        return "pass"
    return "unverified"


def build_sender_auth(
    *auth_lines: Optional[str],
    return_path: Optional[str] = None,
    from_header: Optional[str] = None,
) -> Dict[str, Any]:
    """One call at ingest: turn the raw header evidence into the dict the
    ingester stores in ``email_cache.metadata->'sender_auth'``. Returns an
    empty dict when there is no evidence at all, so a row without one simply
    has no key and consumers fall back to the address comparison.

    ``from_return_path_mismatch`` is the classic spoof fingerprint: the From
    header claims one domain, the envelope says the message came from
    another. Stored as a fact, not a verdict — some legitimate senders relay
    through a different domain — the verdict layer weighs it.
    """
    parsed = parse_auth_results(*auth_lines)
    if not any(parsed.values()):
        return {}
    result: Dict[str, Any] = {
        "spf": parsed["spf"],
        "dkim": parsed["dkim"],
        "dmarc": parsed["dmarc"],
        "status": auth_status(parsed),
    }
    rp_domain = domain_of(return_path)
    fr_domain = domain_of(from_header)
    if rp_domain:
        result["return_path_domain"] = rp_domain
        if fr_domain and rp_domain != fr_domain:
            result["from_return_path_mismatch"] = True
    return result


def verdict(
    *,
    expected_sender: str,
    from_header: Optional[str] = None,
    from_email: Optional[str] = None,
    auth: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One call, the whole question: does this message come from
    ``expected_sender``?

    ``expected_sender`` may be a full address or a bare domain (the relay
    config uses a full address; a rule may name just the domain). ``auth``
    is the stored ``sender_auth`` dict; ``None`` means "no evidence stored",
    in which case the verdict still says what the address comparison says —
    it is only the network part that is ``unverified``.

    Statuses, in order of strength:

    * ``ok``         the address matches and the network evidence passes;
    * ``unverified`` the address matches but the evidence is missing or
                     weak — safe to act on for low-stakes mail, hold
                     credential-shaped mail;
    * ``mismatch``   the address is not the expected one (right domain,
                     wrong box, or a different domain — the ``reasons`` say
                     which, and flag a lookalike when one is found);
    * ``suspicious`` the address matches but the network evidence fails, or
                     the envelope contradicts the From header — an
                     impersonation in progress, never an instruction.
    """
    reasons: List[str] = []
    expected = (expected_sender or "").strip().lower()
    actual = (from_email or "").strip().lower()
    if not actual and from_header:
        match = _ADDR_RE.search(from_header)
        if match:
            actual = match.group(1).strip().lower()
        elif "@" in from_header:
            actual = from_header.split()[-1].strip().lower()

    exp_domain = domain_of(expected)
    act_domain = domain_of(actual)

    address_ok = bool(actual) and (
        actual == expected if "@" in expected else act_domain == expected
    )
    if not address_ok:
        if exp_domain and act_domain == exp_domain:
            reasons.append("expected %s, got %s (same domain, different box)"
                           % (expected, actual or from_header or "?"))
        else:
            reasons.append("sender %s is not the expected %s"
                           % (actual or "?", expected or "?"))
            if is_lookalike_domain(act_domain, exp_domain):
                reasons.append("lookalike domain: %s imitates %s"
                               % (act_domain, exp_domain))
        return {"status": "mismatch", "reasons": reasons,
                "from_email": actual, "auth": auth_status(auth)}

    status = auth_status(auth)
    if status == "fail":
        reasons.append("authentication failed: %s"
                       % {k: v for k, v in (auth or {}).items()
                          if k in ("spf", "dkim", "dmarc") and v})
        return {"status": "suspicious", "reasons": reasons,
                "from_email": actual, "auth": "fail"}
    if status == "pass":
        reasons.append("authentication passed")
    else:
        reasons.append("no usable authentication evidence stored")
    if (auth or {}).get("from_return_path_mismatch"):
        reasons.append("envelope domain (%s) differs from the From header (%s)"
                       % (auth.get("return_path_domain"), act_domain))
        if status == "pass":
            # DMARC passed but the domains disagree — keep the flag, downgrade
            # the confidence: alignment is part of what DMARC is supposed to
            # guarantee, so this combination deserves a human's eye.
            return {"status": "unverified", "reasons": reasons,
                    "from_email": actual, "auth": "pass (unaligned)"}

    return {"status": "ok" if status == "pass" else "unverified",
            "reasons": reasons, "from_email": actual, "auth": status}
