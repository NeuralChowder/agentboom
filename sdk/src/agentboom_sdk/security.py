"""Request-scoped security context.

`public_app_context` is True for the duration of any request served
under the public surface (/public/*). Code that must never run for
public callers — most importantly vault decryption — checks it and
refuses. The gateway (template platform) sets and resets it; nothing
else should.
"""
from contextvars import ContextVar

public_app_context: ContextVar[bool] = ContextVar(
    "public_app_context", default=False)
