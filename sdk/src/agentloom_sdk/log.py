"""Logging setup: one format, one level knob (LOG_LEVEL), stdout."""
import logging
import os
import sys

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    if not _configured:
        level = os.environ.get("LOG_LEVEL", "INFO").upper()
        root = logging.getLogger()
        if not root.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"
            ))
            root.addHandler(handler)
        root.setLevel(getattr(logging, level, logging.INFO))
        _configured = True
    return logging.getLogger(name)
