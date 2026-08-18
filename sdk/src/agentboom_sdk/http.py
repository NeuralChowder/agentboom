"""Serving bytes that came from outside.

Two things go wrong every time a file whose name someone else chose is
handed back over HTTP:

**The name breaks the header.** HTTP headers are latin-1. A filename with an
accent that arrives in decomposed form (a plain letter followed by a
combining mark) cannot be encoded as latin-1, so interpolating it into
`Content-Disposition` raises inside the framework and the whole response
becomes a 500. Every accented attachment becomes undownloadable.

**The bytes get sniffed.** A stored file arrived from a stranger. Served
without `nosniff`, or inline, a crafted SVG or HTML file runs as script
against the origin of an authenticated dashboard.

So neither decision is left to the caller:

    from agentboom_sdk.http import download_headers

    return Response(content=data, media_type="application/octet-stream",
                    headers=download_headers(filename))

`inline=True` exists for the one legitimate case — showing a PDF or an image
in the page rather than sending it to the Downloads folder — and even then
the media type must be one the browser is safe to render, which is why it is
a deliberate argument rather than the default.
"""

from __future__ import annotations

import unicodedata
from typing import Dict
from urllib.parse import quote

#: Types a browser may render in place without handing a stranger a script
#: engine on this origin. SVG is absent on purpose: it is a document format
#: that executes JavaScript.
INLINE_SAFE_TYPES = (
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain",
)


def ascii_fallback(filename: str) -> str:
    """The best plain-ASCII rendering of a filename, for old clients."""
    folded = (unicodedata.normalize("NFKD", filename or "")
              .encode("ascii", "ignore").decode("ascii").strip())
    # Quotes and control characters would end the header value early, which is
    # header injection rather than a display problem.
    folded = "".join(c for c in folded if c.isprintable() and c not in '"\\;')
    return folded or "download"


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """A Content-Disposition that survives any filename.

    Both forms are sent: `filename` in ASCII for anything that does not
    understand RFC 5987, and `filename*` carrying the real UTF-8 name for
    everything that does.
    """
    kind = "inline" if inline else "attachment"
    return (f'{kind}; filename="{ascii_fallback(filename)}"; '
            f"filename*=UTF-8''{quote(filename or 'download', safe='')}")


def download_headers(
    filename: str, *, inline: bool = False, media_type: str = "",
) -> Dict[str, str]:
    """Headers for handing back a file that the system did not author.

    `inline` is downgraded to an attachment for any type a browser would treat
    as active content — the caller asking for inline is expressing a
    preference, not making a security decision.
    """
    if inline and media_type and not any(
            media_type.startswith(t) for t in INLINE_SAFE_TYPES):
        inline = False

    return {
        "Content-Disposition": content_disposition(filename, inline=inline),
        "X-Content-Type-Options": "nosniff",
    }
