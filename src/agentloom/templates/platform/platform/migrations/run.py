#!/usr/bin/env python3
"""Run database migrations. Called at container startup."""
import asyncio

from agentloom_sdk.db import close, run_migrations


async def main():
    print("Running migrations...")
    await run_migrations()
    print("Migrations complete.")
    await close()


if __name__ == "__main__":
    asyncio.run(main())
