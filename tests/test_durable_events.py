"""Tests for the durable event bus (agentboom_sdk.durable_events).

SQLite backend against a real temp database; deliveries go to a minimal
local HTTP server. The PostgreSQL claim path is not exercised here (no
server in CI) — it mirrors the SQLite claim minus SKIP LOCKED.
"""
import asyncio
import json
import os
import pathlib
import re
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="agentboom-durable-events-tests-")
os.environ["DATA_DIR"] = str(pathlib.Path(_TMP) / "data")
os.environ.pop("DATABASE_URI", None)

from agentboom_sdk import db  # noqa: E402
from agentboom_sdk import durable_events  # noqa: E402

_MIGRATIONS = pathlib.Path(_TMP) / "migrations"
_MIGRATIONS.mkdir()
(_MIGRATIONS / "001_events.sql").write_text("""
CREATE TABLE IF NOT EXISTS events_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'platform',
  subject TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  dedupe_key TEXT UNIQUE,
  published_at TEXT
);
CREATE TABLE IF NOT EXISTS events_subscriptions (
  app_name TEXT NOT NULL,
  event_type TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  max_retries INTEGER NOT NULL DEFAULT 5,
  is_enabled INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT,
  PRIMARY KEY (app_name, event_type, endpoint)
);
CREATE TABLE IF NOT EXISTS events_deliveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL,
  app_name TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  max_retries INTEGER NOT NULL DEFAULT 5,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT,
  last_error TEXT,
  response TEXT,
  delivered_at TEXT,
  created_at TEXT,
  UNIQUE (event_id, app_name, endpoint)
);
""")


class _TestServer:
    """Minimal HTTP server: records POST bodies, fails the first N of them."""

    def __init__(self):
        self.posts = []
        self.fail_next = 0
        self._server = None
        self.port = None

    async def start(self):
        async def handle(reader, writer):
            header_blob = await reader.readuntil(b"\r\n\r\n")
            headers = header_blob.decode()
            m = re.search(r"content-length: (\d+)", headers, re.IGNORECASE)
            body = await reader.read(int(m.group(1))) if m else b""
            self.posts.append(json.loads(body or b"{}"))
            if self.fail_next > 0:
                self.fail_next -= 1
                payload = b"nope"
                writer.write(b"HTTP/1.1 500 Internal Server Error\r\n"
                             b"Content-Length: 4\r\n\r\n" + payload)
            else:
                payload = b'{"ok": true}'
                writer.write(b"HTTP/1.1 200 OK\r\n"
                             b"Content-Type: application/json\r\n"
                             b"Content-Length: " + str(len(payload)).encode()
                             + b"\r\n\r\n" + payload)
            await writer.drain()
            writer.close()

        self._server = await asyncio.start_server(handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}/on-event"

    async def stop(self):
        self._server.close()
        await self._server.wait_closed()


class DurableEventsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.loop.run_until_complete(db.run_migrations(_MIGRATIONS))
        cls.server = _TestServer()
        cls.url = cls.loop.run_until_complete(cls.server.start())

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.server.stop())
        cls.loop.run_until_complete(db.close())
        cls.loop.close()

    def setUp(self):
        async def _reset():
            await db.execute("DELETE FROM events_deliveries")
            await db.execute("DELETE FROM events_log")
            await db.execute("DELETE FROM events_subscriptions")
            self.server.posts = []
            self.server.fail_next = 0
        self.loop.run_until_complete(_reset())

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    def test_publish_delivers_to_subscriber(self):
        async def scenario():
            await durable_events.register_subscription(
                "app-a", "email.received", self.url, max_retries=1)
            event_id = await durable_events.publish(
                "email.received", {"from": "x@y.z"},
                source="test", dedupe_key=None, deliver_now=False)
            self.assertIsNotNone(event_id)
            result = await durable_events.drain()
            self.assertEqual(result["attempted"], 1)
            self.assertEqual(len(self.server.posts), 1)
            post = self.server.posts[0]
            self.assertEqual(post["type"], "email.received")
            self.assertEqual(post["payload"], {"from": "x@y.z"})
            row = await db.fetchone(
                "SELECT status, attempts FROM events_deliveries WHERE id IN "
                "(SELECT id FROM events_deliveries) LIMIT 1")
            self.assertEqual(row["status"], "delivered")
            self.assertEqual(row["attempts"], 1)
        self.run_async(scenario())

    def test_dedupe_key_makes_republish_idempotent(self):
        async def scenario():
            await durable_events.register_subscription(
                "app-a", "email.received", self.url)
            first = await durable_events.publish(
                "email.received", {"id": 1}, dedupe_key="gmail-abc",
                deliver_now=False)
            second = await durable_events.publish(
                "email.received", {"id": 1}, dedupe_key="gmail-abc",
                deliver_now=False)
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            await durable_events.drain()
            self.assertEqual(len(self.server.posts), 1)
        self.run_async(scenario())

    def test_wildcard_subscriptions(self):
        async def scenario():
            await durable_events.register_subscription(
                "app-exact", "email.received", self.url)
            await durable_events.register_subscription(
                "app-wild", "email.*", self.url)
            await durable_events.register_subscription("app-all", "*", self.url)
            await durable_events.register_subscription(
                "app-other", "feeds.*", self.url)
            await durable_events.publish("email.received", {},
                                         deliver_now=False)
            result = await durable_events.drain()
            self.assertEqual(result["attempted"], 3)
            self.assertEqual(len(self.server.posts), 3)
        self.run_async(scenario())

    def test_failed_delivery_retries_then_dead(self):
        async def scenario():
            await durable_events.register_subscription(
                "app-a", "mail.bounced", self.url, max_retries=2)
            self.server.fail_next = 10  # every attempt fails
            await durable_events.publish("mail.bounced", {}, deliver_now=False)
            await durable_events.drain()  # attempt 1 -> failed, backoff
            await db.execute(
                "UPDATE events_deliveries SET next_retry_at = '1970-01-01T00:00:00+00:00'")
            await durable_events.drain()  # attempt 2 -> dead
            row = await db.fetchone(
                "SELECT status, attempts, last_error FROM events_deliveries")
            self.assertEqual(row["status"], "dead")
            self.assertEqual(row["attempts"], 2)
            self.assertIn("500", row["last_error"])
            health = await durable_events.health()
            entry = [e for e in health["by_subscriber"]
                     if e["app_name"] == "app-a"][0]
            self.assertEqual(entry["dead"], 1)
        self.run_async(scenario())

    def test_recovery_after_flap(self):
        async def scenario():
            await durable_events.register_subscription(
                "app-a", "mail.bounced", self.url)
            self.server.fail_next = 1
            await durable_events.publish("mail.bounced", {"n": 1},
                                         deliver_now=False)
            await durable_events.drain()  # attempt 1 fails
            row = await db.fetchone("SELECT status FROM events_deliveries")
            self.assertEqual(row["status"], "failed")
            # Backoff elapsed (simulated), server healthy now.
            await db.execute(
                "UPDATE events_deliveries SET next_retry_at = '1970-01-01T00:00:00+00:00'")
            await durable_events.drain()
            row = await db.fetchone("SELECT status FROM events_deliveries")
            self.assertEqual(row["status"], "delivered")
            self.assertEqual(len(self.server.posts), 2)
        self.run_async(scenario())

    def test_concurrent_drains_do_not_double_deliver(self):
        async def scenario():
            for i in range(5):
                await durable_events.register_subscription(
                    f"app-{i}", "bulk.item", self.url)
            await durable_events.publish("bulk.item", {}, deliver_now=False)
            results = await asyncio.gather(
                durable_events.drain(), durable_events.drain())
            self.assertEqual(sum(r["attempted"] for r in results), 5)
            self.assertEqual(len(self.server.posts), 5)
            statuses = [r["status"] for r in await db.fetchall(
                "SELECT status FROM events_deliveries")]
            self.assertTrue(all(s == "delivered" for s in statuses))
        self.run_async(scenario())

    def test_replay_redelivers_past_event(self):
        async def scenario():
            await durable_events.register_subscription(
                "app-a", "report.ready", self.url)
            event_id = await durable_events.publish("report.ready",
                                                    {"r": 1}, deliver_now=False)
            await durable_events.drain()
            self.assertEqual(len(self.server.posts), 1)
            result = await durable_events.replay(event_id)
            self.assertEqual(result["queued"], 1)
            self.assertEqual(len(self.server.posts), 2)
        self.run_async(scenario())

    def test_replay_unknown_event_raises(self):
        async def scenario():
            with self.assertRaises(KeyError):
                await durable_events.replay(99999)
        self.run_async(scenario())

    def test_resolve_endpoint(self):
        self.assertEqual(
            durable_events._resolve_endpoint("/api/x/on-y"),
            durable_events.INTERNAL_URL.rstrip("/") + "/api/x/on-y")
        self.assertEqual(
            durable_events._resolve_endpoint("https://example.com/hook"),
            "https://example.com/hook")

    def test_recent_events_filters(self):
        async def scenario():
            await durable_events.publish("a.one", {}, subject="s1",
                                         deliver_now=False)
            await durable_events.publish("a.two", {}, subject="s2",
                                         deliver_now=False)
            rows = await durable_events.recent_events(type="a.two")
            self.assertEqual(len(rows), 1)
            rows = await durable_events.recent_events(subject="s1")
            self.assertEqual(len(rows), 1)
            rows = await durable_events.recent_events(limit=1)
            self.assertEqual(len(rows), 1)
        self.run_async(scenario())


def tearDownModule():
    # A contended asyncio.Lock binds itself to the loop that first waits on
    # it. The concurrent-drain test above does exactly that on this module's
    # (now closed) loop, which would trip every later contended test in the
    # suite (test_sdk's). A fresh, unbound lock restores the pristine state.
    db._op_lock._lock = asyncio.Lock()


if __name__ == "__main__":
    unittest.main()
