#!/usr/bin/env python3
# agentloom:managed — upgraded by `agentloom upgrade`; local edits become drift.
"""Prune agent chat transcripts to bound disk growth.

Qwen Code keeps full chat transcripts under ~/.qwen/projects/*/chats/*.jsonl
and they grow without limit. This keeps only the newest KEEP_TRANSCRIPTS
per project directory. Run periodically from entrypoint.sh.

Deliberately conservative: only *.jsonl inside chats/ directories is ever
touched, and only files beyond the keep window.
"""
import os
import sys
from pathlib import Path

KEEP = int(os.environ.get("KEEP_TRANSCRIPTS", "20"))
QWEN_HOME = Path(os.environ.get("QWEN_HOME", str(Path.home() / ".qwen")))


def main() -> int:
    projects = QWEN_HOME / "projects"
    if not projects.is_dir():
        return 0
    removed = 0
    for chats_dir in projects.glob("*/chats"):
        if not chats_dir.is_dir():
            continue
        transcripts = sorted(
            (p for p in chats_dir.glob("*.jsonl") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for victim in transcripts[KEEP:]:
            try:
                victim.unlink()
                removed += 1
            except OSError as exc:
                print(f"prune: could not remove {victim}: {exc}", file=sys.stderr)
    if removed:
        print(f"prune: removed {removed} old transcripts (keep={KEEP})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
