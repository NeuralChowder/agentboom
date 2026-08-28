"""Tests for the leased work-queue discipline (agentboom_sdk.workqueue).

SQLite backend against a real temp database. The PostgreSQL claim path
mirrors the SQLite one minus SKIP LOCKED and is compile-checked only.
"""
import asyncio
import os
import pathlib
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="agentboom-workqueue-tests-")
os.environ["DATA_DIR"] = str(pathlib.Path(_TMP) / "data")
os.environ.pop("DATABASE_URI", None)

from agentboom_sdk import db  # noqa: E402
from agentboom_sdk.workqueue import WorkQueue  # noqa: E402

_MIGRATIONS = pathlib.Path(_TMP) / "migrations"
_MIGRATIONS.mkdir()
(_MIGRATIONS / "001_wq.sql").write_text("""
CREATE TABLE IF NOT EXISTS work_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  lease_until TEXT,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  claim_token TEXT,
  result TEXT,
  created_at TEXT
);
""")


class WorkQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.loop.run_until_complete(db.run_migrations(_MIGRATIONS))
        cls.q = WorkQueue(table="work_items", name="test",
                          lease_sec=30, max_attempts=2)

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(db.close())
        cls.loop.close()

    def setUp(self):
        async def _reset():
            await db.execute("DELETE FROM work_items")
        self.loop.run_until_complete(_reset())

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    async def _insert(self, payload="job"):
        await db.execute(
            "INSERT INTO work_items (payload, created_at) VALUES (?, ?)",
            (payload, "2026-01-01T00:00:00+00:00"))
        return (await db.fetchone(
            "SELECT id FROM work_items ORDER BY id DESC LIMIT 1"))["id"]

    def test_claim_stamps_lease_and_token(self):
        async def scenario():
            item_id = await self._insert()
            row = await self.q.claim()
            self.assertIsNotNone(row)
            self.assertEqual(row["id"], item_id)
            self.assertEqual(row["status"], "running")
            self.assertEqual(row["attempts"], 1)
            self.assertIsNotNone(row["claim_token"])
            self.assertIsNotNone(row["lease_until"])
        self.run_async(scenario())

    def test_active_lease_blocks_reclaim(self):
        async def scenario():
            await self._insert()
            first = await self.q.claim()
            self.assertIsNotNone(first)
            # Lease alive: nobody else can take it.
            second = await self.q.claim()
            self.assertIsNone(second)
            # Lease lapsed: the holder is gone — reclaimable.
            await db.execute(
                "UPDATE work_items SET lease_until = '1970-01-01T00:00:00+00:00'")
            third = await self.q.claim()
            self.assertIsNotNone(third)
            self.assertEqual(third["attempts"], 2)
            self.assertNotEqual(third["claim_token"], first["claim_token"])
        self.run_async(scenario())

    def test_exhausted_orphan_not_reclaimed(self):
        async def scenario():
            await self._insert()
            await self.q.claim()  # attempt 1
            await db.execute(
                "UPDATE work_items SET lease_until = '1970-01-01T00:00:00+00:00'")
            await self.q.claim()  # attempt 2 = max
            await db.execute(
                "UPDATE work_items SET lease_until = '1970-01-01T00:00:00+00:00'")
            self.assertIsNone(await self.q.claim())
        self.run_async(scenario())

    def test_refresh_guarded_by_token(self):
        async def scenario():
            await self._insert()
            row = await self.q.claim()
            self.assertTrue(await self.q.refresh(row["id"], row["claim_token"]))
            self.assertFalse(await self.q.refresh(row["id"], "stale-token"))
        self.run_async(scenario())

    def test_release_refunds_attempt(self):
        async def scenario():
            await self._insert()
            row = await self.q.claim()
            self.assertTrue(await self.q.release(row["id"], row["claim_token"]))
            fresh = await db.fetchone("SELECT * FROM work_items")
            self.assertEqual(fresh["status"], "pending")
            self.assertEqual(fresh["attempts"], 0)
            self.assertIsNone(fresh["claim_token"])
        self.run_async(scenario())

    def test_complete_requires_current_token(self):
        async def scenario():
            await self._insert()
            row = await self.q.claim()
            self.assertFalse(await self.q.complete(row["id"], "stale-token",
                                                   "done"))
            self.assertTrue(await self.q.complete(row["id"], row["claim_token"],
                                                  "done"))
            fresh = await db.fetchone("SELECT * FROM work_items")
            self.assertEqual(fresh["status"], "done")
            self.assertEqual(fresh["result"], "done")
        self.run_async(scenario())

    def test_fail_walks_the_retry_budget(self):
        async def scenario():
            await self._insert()
            row = await self.q.claim()  # attempt 1
            state = await self.q.fail(row["id"], row["claim_token"], "boom")
            self.assertEqual(state, "pending")
            row = await self.q.claim()  # attempt 2
            state = await self.q.fail(row["id"], row["claim_token"], "boom")
            self.assertEqual(state, "failed")
        self.run_async(scenario())

    def test_fail_refund_spends_no_budget(self):
        async def scenario():
            await self._insert()
            row = await self.q.claim()
            state = await self.q.fail(row["id"], row["claim_token"],
                                      "agent offline", refund=True)
            self.assertEqual(state, "pending")
            fresh = await db.fetchone("SELECT attempts FROM work_items")
            self.assertEqual(fresh["attempts"], 0)
        self.run_async(scenario())

    def test_fail_non_retryable_is_terminal(self):
        async def scenario():
            await self._insert()
            row = await self.q.claim()
            state = await self.q.fail(row["id"], row["claim_token"], "boom",
                                      retryable=False)
            self.assertEqual(state, "failed")
        self.run_async(scenario())

    def test_reclaim_orphans_settles_both_ways(self):
        async def scenario():
            a = await self._insert("retryable")
            b = await self._insert("exhausted")
            ra = await self.q.claim()
            rb = await self.q.claim()  # different row; b at attempt 1
            await db.execute(
                "UPDATE work_items SET lease_until = '1970-01-01T00:00:00+00:00' "
                "WHERE id IN (?, ?)", (a, b))
            # Burn b's second attempt so it is exhausted.
            await db.execute(
                "UPDATE work_items SET attempts = 2 WHERE id = ?", (b,))
            n = await self.q.reclaim_orphans()
            self.assertEqual(n, 2)
            sa = await db.fetchone(
                "SELECT status FROM work_items WHERE id = ?", (a,))
            sb = await db.fetchone(
                "SELECT status, error FROM work_items WHERE id = ?", (b,))
            self.assertEqual(sa["status"], "pending")
            self.assertEqual(sb["status"], "failed")
            self.assertIn("abandoned", sb["error"])
        self.run_async(scenario())


def tearDownModule():
    # See test_durable_events.tearDownModule: a contended asyncio.Lock binds
    # to the loop that first waits on it; restore the pristine, unbound lock.
    db._op_lock._lock = asyncio.Lock()


if __name__ == "__main__":
    unittest.main()
