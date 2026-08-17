#!/usr/bin/env python3
# agentloom:managed — upgraded by `agentloom upgrade`; local edits become drift.
"""Run database migrations. Called at container startup."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sdk.db import close, run_migrations  # noqa: E402


async def main():
    print("Running migrations...")
    await run_migrations()
    print("Migrations complete.")
    await close()


if __name__ == "__main__":
    asyncio.run(main())
