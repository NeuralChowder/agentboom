"""One shape for "started, not finished".

Several endpoints here return before their work is done — a lookup, a
promotion pass, a file being read. Each had invented its own way of saying so,
and every one of them said it in prose:

    {"ok": true, "message": "Looking up Acme on the web. It runs behind 48
     other agent turn(s); whatever is found appears on the profile when it
     lands."}

A person can read that. A caller cannot act on it. There is no id to hold, no
address to ask again, and nothing that distinguishes "accepted and queued"
from "done" except the wording — which is exactly how a button that worked
came to look like a button that did nothing, twice.

So: one envelope, and the two fields that matter are `job_id` and
`status_url`.

    accepted(job_id=41, status_url="/api/activity/queue/41",
             what="Looking up Acme on the web", queued_behind=3)

`status_url` is relative when it is on this gateway and absolute when it is
not, because a caller should never have to know which. `poll_after_ms` is a
hint rather than a rule — long work says "ask in thirty seconds" instead of
being polled thirty times for nothing.

The prose stays. It is not a substitute for the fields, and the fields are not
a substitute for it: one is for the agent, the other is for the person reading
a screen, and dropping either is how you end up serving only half of them.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def accepted(
    *,
    job_id: Any,
    status_url: str,
    what: str,
    queued_behind: Optional[int] = None,
    poll_after_ms: int = 3000,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The response for work that has been accepted and has not finished.

    `queued_behind` is how many things run before this one — 0 means next.
    Passing None means it is not queued behind anything countable, which is
    different from zero and should not be reported as "next".
    """
    position = ""
    if queued_behind == 0:
        position = " It runs next."
    elif queued_behind:
        position = f" {queued_behind} other job(s) run before it."

    body: Dict[str, Any] = {
        "ok": True,
        # Present and true on every async response, so a caller can branch on
        # one field rather than on the absence of a result.
        "accepted": True,
        "done": False,
        "job_id": job_id,
        "status": "queued",
        "status_url": status_url,
        "poll_after_ms": poll_after_ms,
        "queued_behind": queued_behind,
        "message": (
            f"{what} — started, not finished.{position} "
            f"Poll {status_url} for progress; it reports done or failed when it lands."
        ),
    }
    if extra:
        body.update(extra)
    return body


def already_running(*, job_id: Any, status_url: str, what: str) -> Dict[str, Any]:
    """The response when the same work is already in flight.

    Not an error, and deliberately the same shape as `accepted` — a caller that
    clicked twice wants the id of the run that is happening, not a refusal it
    has to special-case.
    """
    return {
        "ok": True,
        "accepted": True,
        "done": False,
        "duplicate": True,
        "job_id": job_id,
        "status": "running",
        "status_url": status_url,
        "poll_after_ms": 3000,
        "queued_behind": None,
        "message": (f"{what} is already running — nothing new was started. "
                    f"Poll {status_url} for progress."),
    }
