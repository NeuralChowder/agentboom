"""Tests for the self-evolve package (agentboom package: self-evolve).

Conventions per tests/test_durable_events.py: DATA_DIR temp + pop
DATABASE_URI before agentboom_sdk.db is imported, per-class loop +
run_async, tearDownModule resetting db._op_lock._lock.

The mini-app is loaded from the template source (bytecode off — the
installer copies every file in the template tree) against a real temp
SQLite database with the package's own migration applied. The agent
queue is stubbed (no LLM turn is ever started), and the events /
scheduler tables are deliberately absent to prove the fault-isolated
registry degrades instead of wedging.
"""
import asyncio
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from datetime import datetime

_TMP = tempfile.mkdtemp(prefix="agentboom-selfevolve-tests-")
os.environ["DATA_DIR"] = str(pathlib.Path(_TMP) / "data")
os.environ.pop("DATABASE_URI", None)
for _k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
           "LLM_BASE_URL", "LLM_API_KEY"):
    os.environ.pop(_k, None)

from agentboom_sdk import db  # noqa: E402
from fastapi import HTTPException  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PKG = REPO_ROOT / "src/agentboom/templates/packages/self-evolve"
_MAIN = _PKG / "platform/miniapps/self-evolve/main.py"


def _load_miniapp():
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "selfevolve_miniapp_under_test", _MAIN)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


mod = _load_miniapp()


class _FakeAgent:
    """Stands in for agentboom_sdk.agent: records turns, no LLM."""

    def __init__(self):
        self.calls = []
        self.answer = "Done — change implemented and verified end-to-end."
        self.depth = 0

    def stats(self):
        return {"active": self.depth, "queued": 0}

    async def ask(self, prompt, *, conversation=None, timeout=120,
                  priority="normal"):
        self.calls.append({"prompt": prompt, "timeout": timeout,
                           "priority": priority})
        return self.answer


class SelfEvolvePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Pristine state: a previous test module (test_sdk) may have left
        # the shared asyncio.Lock bound to its own (now closed) loop after
        # a contended acquire — the slow-path acquire then raises 'bound
        # to a different event loop' even when the lock is free. A fresh,
        # unbound lock restores the pristine state.
        db._op_lock._lock = asyncio.Lock()
        cls.loop = asyncio.new_event_loop()
        cls.fake_agent = _FakeAgent()
        cls.orig_agent = mod.agent
        mod.agent = cls.fake_agent
        migs = pathlib.Path(_TMP) / "migrations"
        migs.mkdir()
        (migs / "019_self_evolve.sql").write_text(
            (_PKG / "platform/migrations/019_self_evolve.sql").read_text())
        cls.loop.run_until_complete(db.run_migrations(migs))

    @classmethod
    def tearDownClass(cls):
        mod.agent = cls.orig_agent
        cls.loop.run_until_complete(db.close())
        cls.loop.close()

    def setUp(self):
        async def _reset():
            for t in ("selfevolve_runs", "selfevolve_backlog",
                      "selfevolve_repair_requests", "selfevolve_metrics",
                      "selfevolve_friction", "selfevolve_change_outcomes",
                      "selfevolve_guardrail_alerts", "selfevolve_settings"):
                await db.execute(f"DELETE FROM {t}")
            self.fake_agent.calls.clear()
            self.fake_agent.answer = (
                "Done — change implemented and verified end-to-end.")
            self.fake_agent.depth = 0
            await self._settle_inflight()
        self.loop.run_until_complete(_reset())

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)

    async def _settle_inflight(self):
        """Let the background turns this test spawned run to completion.

        The db layer multiplexes through one aiosqlite worker THREAD, so a
        tight sleep(0) spin can burn its whole budget before the thread is
        even scheduled on a loaded box — sleep a few ms per round instead."""
        deadline = time.monotonic() + 10
        while mod._inflight_tasks and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        self.assertFalse(
            mod._inflight_tasks,
            f"in-flight task did not settle: {mod._inflight_tasks}")

    def _fake_local_now(self, hour):
        return (datetime.now().astimezone()
                .replace(hour=hour, minute=0, second=0, microsecond=0))

    # ── onboarding guard ──────────────────────────────────────────────

    def test_runs_409_until_enabled(self):
        async def scenario():
            health = await mod.health()
            self.assertEqual(health["status"], "ok")
            self.assertFalse(health["enabled"])
            with self.assertRaises(HTTPException) as ctx:
                await mod.start_run(mod.RunStart(trigger="manual"))
            self.assertEqual(ctx.exception.status_code, 409)
            res = await mod.update_settings_route(
                mod.SettingsUpdate(enabled=True))
            self.assertTrue(res["settings"]["enabled"])
            started = await mod.start_run(mod.RunStart(trigger="manual"))
            self.assertTrue(started["ok"])
            row = await db.fetchone(
                "SELECT * FROM selfevolve_runs WHERE id = $1",
                started["run"]["id"])
            self.assertEqual(row["status"], "running")
        self.run_async(scenario())

    # ── settings: seed + validation ───────────────────────────────────

    def test_settings_seeded_with_defaults(self):
        async def scenario():
            s = await mod.get_settings_route()
            for key, value in mod.SETTING_DEFAULTS.items():
                self.assertEqual(s[key], value, key)
        self.run_async(scenario())

    def test_settings_range_validation(self):
        async def scenario():
            bad = (mod.SettingsUpdate(inflight_stale_hours=0),
                   mod.SettingsUpdate(inflight_stale_hours=200),
                   mod.SettingsUpdate(implement_timeout_sec=59),
                   mod.SettingsUpdate(drain_cutoff_hour=24),
                   mod.SettingsUpdate(drain_max_per_night=13),
                   mod.SettingsUpdate(notify_max_per_hour=0),
                   mod.SettingsUpdate(notify_max_chars=100),
                   mod.SettingsUpdate(outcome_measure_days=15),
                   mod.SettingsUpdate(regression_tolerance=3.5))
            for req in bad:
                with self.assertRaises(HTTPException) as ctx:
                    await mod.update_settings_route(req)
                self.assertEqual(ctx.exception.status_code, 400)
            res = await mod.update_settings_route(
                mod.SettingsUpdate(inflight_stale_hours=24,
                                   drain_cutoff_hour=4,
                                   notify_max_per_hour=5))
            s = res["settings"]
            self.assertEqual(s["inflight_stale_hours"], 24)
            self.assertEqual(s["drain_cutoff_hour"], 4)
            self.assertEqual(s["notify_max_per_hour"], 5)
            # untouched knobs keep their defaults
            self.assertEqual(s["implement_timeout_sec"], 1800)
            res = await mod.update_settings_route(
                mod.SettingsUpdate(enabled="yes"))
            self.assertIs(res["settings"]["enabled"], True)
        self.run_async(scenario())

    # ── backlog: add / dedupe / dismiss ───────────────────────────────

    def test_backlog_add_dedupe_and_dismiss(self):
        async def scenario():
            first = await mod.add_backlog(mod.BacklogAdd(
                title="Improve the health page",
                why="Users could not tell at a glance what was down.",
                tier="autonomous"))
            self.assertFalse(first["duplicate"])
            item_id = first["item"]["id"]
            dup = await mod.add_backlog(mod.BacklogAdd(
                title="improve the HEALTH page",
                why="Same idea re-proposed, in different case.",
                tier="autonomous"))
            self.assertTrue(dup["duplicate"])
            self.assertEqual(dup["item"]["id"], item_id)
            dismissed = await mod.dismiss_backlog(
                item_id, mod.DismissReason(reason="no longer relevant"))
            self.assertEqual(dismissed["item"]["status"], "dismissed")
            row = await db.fetchone(
                "SELECT resolution FROM selfevolve_backlog WHERE id = $1",
                item_id)
            self.assertEqual(row["resolution"],
                             "Dismissed: no longer relevant")
            # a dismissed idea is not re-proposed against — a re-add
            # creates a fresh row, it does not resurrect the old one
            again = await mod.add_backlog(mod.BacklogAdd(
                title="improve the health page",
                why="It came back with evidence this time, really.",
                tier="autonomous"))
            self.assertFalse(again["duplicate"])
            self.assertNotEqual(again["item"]["id"], item_id)
        self.run_async(scenario())

    # ── adopt: user override, high priority, A-to-Z contract ──────────

    def test_adopt_implements_and_adopts(self):
        async def scenario():
            added = await mod.add_backlog(mod.BacklogAdd(
                title="Cache the catalog in memory",
                why="The nightly run reads it five times over.",
                tier="autonomous"))
            item_id = added["item"]["id"]
            res = await mod.adopt_backlog(item_id)
            self.assertEqual(res, {"ok": True, "status": "in-flight"})
            row = await db.fetchone(
                "SELECT status FROM selfevolve_backlog WHERE id = $1",
                item_id)
            self.assertEqual(row["status"], "in-flight")
            await self._settle_inflight()
            row = await db.fetchone(
                "SELECT status, resolution FROM selfevolve_backlog "
                "WHERE id = $1", item_id)
            self.assertEqual(row["status"], "adopted")
            self.assertIn("verified end-to-end", row["resolution"])
            call = self.fake_agent.calls[0]
            self.assertEqual(call["priority"], "high")
            self.assertEqual(call["timeout"], 1800.0)
            self.assertIn("ITEM #%s" % item_id, call["prompt"])
        self.run_async(scenario())

    # ── INCOMPLETE contract: retry, then stop auto-claiming ───────────

    def test_incomplete_answer_retries_then_stops(self):
        async def scenario():
            added = await mod.add_backlog(mod.BacklogAdd(
                title="Split the giant migration into two",
                why="It times out on the slow disk under load.",
                tier="autonomous"))
            item_id = added["item"]["id"]
            self.fake_agent.answer = "INCOMPLETE: the split is half done."
            tick1 = await mod.backlog_tick(force=True)
            self.assertIsNotNone(tick1["claimed"])
            await self._settle_inflight()
            row = await db.fetchone(
                "SELECT status, drain_attempts FROM selfevolve_backlog "
                "WHERE id = $1", item_id)
            self.assertEqual(row["status"], "open")
            self.assertEqual(row["drain_attempts"], 1)
            tick2 = await mod.backlog_tick(force=True)
            self.assertIsNotNone(tick2["claimed"])
            await self._settle_inflight()
            row = await db.fetchone(
                "SELECT status, drain_attempts FROM selfevolve_backlog "
                "WHERE id = $1", item_id)
            self.assertEqual(row["status"], "open")
            self.assertEqual(row["drain_attempts"], 2)
            # attempt budget spent: the drain no longer claims it
            tick3 = await mod.backlog_tick(force=True)
            self.assertIsNone(tick3["claimed"])
            self.assertIn("no open autonomous items", tick3["skipped"])
            # the drain runs at LOW priority, never ahead of ops work
            for call in self.fake_agent.calls:
                self.assertEqual(call["priority"], "low")
        self.run_async(scenario())

    # ── drain decisions: queue depth, window, cap ─────────────────────

    def test_drain_window_cap_and_depth(self):
        async def scenario():
            orig_local_now = mod._local_now
            try:
                # queue depth first: the shared agent queue is busy
                mod._local_now = lambda: self._fake_local_now(3)
                self.fake_agent.depth = 2
                res = await mod.backlog_tick(force=False)
                self.assertIsNone(res["claimed"])
                self.assertIn("agent queue busy", res["skipped"])
                self.fake_agent.depth = 0
                # window closed: past the cutoff hour the shift is over
                await mod.update_settings_route(
                    mod.SettingsUpdate(drain_cutoff_hour=0))
                res = await mod.backlog_tick(force=False)
                self.assertIsNone(res["claimed"])
                self.assertIn("window closed", res["skipped"])
                self.assertFalse(res["window"]["open"])
                # cap: one item already started tonight, cap of 1
                await mod.update_settings_route(
                    mod.SettingsUpdate(drain_cutoff_hour=5,
                                       drain_max_per_night=1))
                await db.execute(
                    """INSERT INTO selfevolve_backlog
                         (title, why, tier, status, drain_attempts,
                          created_at, updated_at)
                       VALUES ('An already-built item',
                               'Adopted earlier tonight, already done.',
                               'autonomous', 'adopted', 1, $1, $1)""",
                    mod._now())
                res = await mod.backlog_tick(force=False)
                self.assertIsNone(res["claimed"])
                self.assertIn("cap reached", res["skipped"])
                self.assertEqual(res["window"]["drained_today"], 1)
            finally:
                mod._local_now = orig_local_now
        self.run_async(scenario())

    # ── stale in-flight reclaim: the gateway-restart recovery ─────────

    def test_stale_inflight_reclaimed_and_retried(self):
        async def scenario():
            orig_local_now = mod._local_now
            try:
                added = await mod.add_backlog(mod.BacklogAdd(
                    title="Index the runs table by started_at",
                    why="The nightly run scans it fully on big agents.",
                    tier="autonomous"))
                item_id = added["item"]["id"]
                # the worker died 13 hours ago (default reclaim: 12h)
                await db.execute(
                    """UPDATE selfevolve_backlog
                           SET status = 'in-flight', updated_at = $2
                         WHERE id = $1""",
                    item_id, mod._ago(hours=13))
                mod._local_now = lambda: self._fake_local_now(3)
                res = await mod.backlog_tick(force=False)
                self.assertEqual(
                    [s["id"] for s in res["reconciled"]], [item_id])
                self.assertEqual(res["reconciled"][0]["verdict"],
                                 "stale_reclaimed")
                # the reclaimed item is claimable again in the same pass
                self.assertIsNotNone(res["claimed"])
                await self._settle_inflight()
                row = await db.fetchone(
                    "SELECT status FROM selfevolve_backlog WHERE id = $1",
                    item_id)
                self.assertEqual(row["status"], "adopted")
            finally:
                mod._local_now = orig_local_now
        self.run_async(scenario())

    # ── repair loop: deduped intake, verdicts, dismissal ──────────────

    def test_repair_upsert_dedupe_and_dismiss(self):
        async def scenario():
            created = await mod._upsert_request(
                "schedule_job", 5, "boom", "job:app-a:digest")
            self.assertEqual(created, 1)
            created = await mod._upsert_request(
                "schedule_job", 5, "boom again", "job:app-a:digest")
            self.assertEqual(created, 1)  # same signature: counted, not new
            row = await db.fetchone(
                "SELECT id, count, state FROM selfevolve_repair_requests "
                "WHERE fingerprint = 'job:app-a:digest'")
            self.assertEqual(row["count"], 2)
            res = await mod.dismiss_repair_request(
                row["id"], mod.RepairDismiss(reason="expected churn"))
            self.assertEqual(res["item"]["state"], "expected")
            # a dismissed signature is never re-triggered
            created = await mod._upsert_request(
                "schedule_job", 5, "boom", "job:app-a:digest")
            self.assertEqual(created, 0)
            with self.assertRaises(HTTPException) as ctx:
                await mod.dismiss_repair_request(row["id"])
            self.assertEqual(ctx.exception.status_code, 409)
        self.run_async(scenario())

    def test_repair_tick_fix_and_escalation(self):
        async def scenario():
            now = mod._now()
            await db.execute(
                """INSERT INTO selfevolve_repair_requests
                     (kind, target_id, fingerprint, error, state, count,
                      attempts, first_seen, last_seen, updated_at)
                   VALUES ('schedule_job', 7, 'job:app-a:jobs',
                           '500 from /api/jobs/run', 'requested', 9, 0,
                           $1, $1, $1)""", now)
            self.fake_agent.answer = json.dumps(
                {"verdict": "fixed", "reason": "missing index",
                 "fix": "added the index, re-ran the failing run"})
            tick = await mod.repair_tick()
            self.assertIsNotNone(tick["claimed"])
            await self._settle_inflight()
            row = await db.fetchone(
                "SELECT state, attempts FROM selfevolve_repair_requests "
                "WHERE fingerprint = 'job:app-a:jobs'")
            self.assertEqual(row["state"], "resolved")
            self.assertEqual(row["attempts"], 1)
            # a turn that cannot close the request escalates to backlog
            await db.execute(
                """INSERT INTO selfevolve_repair_requests
                     (kind, target_id, fingerprint, error, state, count,
                      attempts, first_seen, last_seen, updated_at)
                   VALUES ('event_delivery', 0, 'event:app-b:on-mail',
                           'dead after retries', 'requested', 9, 0,
                           $1, $1, $1)""", now)
            self.fake_agent.answer = json.dumps(
                {"verdict": "escalated", "reason": "root cause is the "
                 "subscriber's own timeout policy",
                 "root_cause": "raise the subscriber timeout, then "
                 "re-deliver the dead batch",
                 "tier": "proposal"})
            tick = await mod.repair_tick()
            self.assertIsNotNone(tick["claimed"])
            await self._settle_inflight()
            row = await db.fetchone(
                "SELECT state, backlog_id FROM selfevolve_repair_requests "
                "WHERE fingerprint = 'event:app-b:on-mail'")
            self.assertEqual(row["state"], "escalated")
            backlog = await db.fetchone(
                "SELECT title, tier FROM selfevolve_backlog "
                "WHERE id = $1", row["backlog_id"])
            self.assertEqual(backlog["tier"], "proposal")
            self.assertIn("event_delivery", backlog["title"])
        self.run_async(scenario())

    # ── metrics: fault isolation + the model probe is env-driven ──────

    def test_metrics_sample_fault_isolation(self):
        async def scenario():
            res = await mod.metrics_sample()
            self.assertTrue(res["ok"])
            # own-table metrics always work
            self.assertIn("repair_open", res["metrics"])
            self.assertIn("friction_7d", res["metrics"])
            self.assertIn("queues_stalled", res["metrics"])
            self.assertEqual(res["metrics"]["queues_stalled"], 0.0)
            # cross-app sources (events package, scheduler) may or may
            # not be installed in this DB — in the full suite earlier
            # test modules have applied their migrations to the same
            # shared SQLite file. What is invariant: only those external
            # sources may fail, and each source is failed OR sampled.
            external = {"events_dead", "jobs_failing", "jobs_fired_24h"}
            self.assertTrue(set(res["failed"]) <= external)
            self.assertFalse(set(res["failed"]) & set(res["metrics"]))
            for name in external - set(res["failed"]):
                self.assertIn(name, res["metrics"])
            # no LLM configured -> no probe sample at all
            self.assertNotIn("model_probe_ms", res["metrics"])
            latest = await mod.metrics_latest(hours=24)
            names = {m["name"] for m in latest["metrics"]}
            self.assertTrue({"repair_open", "queues_stalled"} <= names)
        self.run_async(scenario())

    # ── selection: noise-aware verdicts + guardrail cooldown ──────────

    def test_reconcile_judges_and_guardrails(self):
        async def scenario():
            now, old = mod._now(), mod._ago(days=4)
            await db.execute(
                """INSERT INTO selfevolve_change_outcomes
                     (run_id, change_summary, metric_name, direction,
                      baseline_value, verdict, created_at)
                   VALUES (1, 'tightened the retry budget', 'repair_open',
                           'down', 10.0, 'pending', $1)""", old)
            await db.execute(
                "INSERT INTO selfevolve_metrics (name, value, sampled_at) "
                "VALUES ('repair_open', 50.0, $1)", now)
            await db.execute(
                "INSERT INTO selfevolve_metrics (name, value, sampled_at) "
                "VALUES ('jobs_failing', 10.0, $1)", old)
            await db.execute(
                "INSERT INTO selfevolve_metrics (name, value, sampled_at) "
                "VALUES ('jobs_failing', 50.0, $1)", now)
            first = await mod.reconcile_outcomes()
            self.assertEqual(first["judged"], 1)
            judged = first["results"][0]
            self.assertEqual(judged["verdict"], "regressed")
            oid = judged["id"]
            # the guardrail watch raised exactly one alert, and the 72h
            # cooldown dedupes it on the next pass
            self.assertEqual(
                [a["metric_name"] for a in first["guardrail_alerts"]],
                ["jobs_failing"])
            second = await mod.reconcile_outcomes()
            self.assertEqual(second["guardrail_alerts"], [])
            n_alerts = int(await db.fetchval(
                "SELECT COUNT(*) FROM selfevolve_guardrail_alerts"))
            self.assertEqual(n_alerts, 1)
            # the explicit-instruction revert path, and only from
            # 'regressed'
            reverted = await mod.mark_outcome_reverted(
                oid, mod.OutcomeReverted(commit="deadbeefcafe"))
            self.assertEqual(reverted["outcome"]["verdict"], "reverted")
            with self.assertRaises(HTTPException) as ctx:
                await mod.mark_outcome_reverted(
                    oid, mod.OutcomeReverted(commit="deadbeefcafe"))
            self.assertEqual(ctx.exception.status_code, 409)
            summary = await mod.outcomes_summary()
            self.assertEqual(
                {r["verdict"]: r["n"] for r in summary["by_verdict"]},
                {"reverted": 1})
        self.run_async(scenario())

    # ── runs: finish records declared outcomes ────────────────────────

    def test_run_finish_records_expected_metrics(self):
        async def scenario():
            await mod.update_settings_route(mod.SettingsUpdate(enabled=True))
            started = await mod.start_run(mod.RunStart(trigger="schedule"))
            run_id = started["run"]["id"]
            res = await mod.finish_run(
                run_id,
                mod.RunFinish(
                    findings="the repair backlog was stuck",
                    changes="reclaimed stale in-flight rows",
                    message_sent=False,
                    genome_commit="abc123def456",
                    expected_metrics=[mod.ExpectedMetric(
                        name="repair_open", direction="down",
                        baseline=4.0)]))
            self.assertEqual(res["run"]["status"], "done")
            row = await db.fetchone(
                "SELECT * FROM selfevolve_change_outcomes WHERE run_id = $1",
                run_id)
            self.assertEqual(row["verdict"], "pending")
            self.assertEqual(row["genome_commit"], "abc123def456")
            self.assertEqual(row["metric_name"], "repair_open")
            self.assertEqual(float(row["baseline_value"]), 4.0)
        self.run_async(scenario())

    # ── notify: channel order, rate limit, attribution ────────────────

    def test_notify_channels_and_limits(self):
        async def scenario():
            # too long -> 429 before anything is sent
            with self.assertRaises(HTTPException) as ctx:
                await mod.notify(mod.Notify(text="x" * 2000))
            self.assertEqual(ctx.exception.status_code, 429)
            # neither ntfy nor Telegram configured -> 503
            with self.assertRaises(HTTPException) as ctx:
                await mod.notify(mod.Notify(text="hello"))
            self.assertEqual(ctx.exception.status_code, 503)
            self.assertIn("no notification channel",
                          str(ctx.exception.detail))
            # rate limit: three messages already sent this hour
            for _ in range(3):
                await db.execute(
                    """INSERT INTO selfevolve_runs
                         (trigger, started_at, status, message_sent)
                       VALUES ('schedule', $1, 'done', 1)""", mod._now())
            with self.assertRaises(HTTPException) as ctx:
                await mod.notify(mod.Notify(text="hello"))
            self.assertEqual(ctx.exception.status_code, 429)
            self.assertIn("rate limit", str(ctx.exception.detail))
        self.run_async(scenario())

    def test_notify_via_telegram_env_attributes_to_run(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
        os.environ["TELEGRAM_CHAT_ID"] = "42"
        sent = []

        class _Resp:
            status_code = 200
            text = "ok"

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None):
                sent.append((url, json))
                return _Resp()

        orig = mod.httpx.AsyncClient
        mod.httpx.AsyncClient = _Client
        try:
            async def scenario():
                await db.execute(
                    """INSERT INTO selfevolve_runs
                         (trigger, started_at, status, message_sent)
                       VALUES ('schedule', $1, 'running', 0)""",
                    mod._now())
                res = await mod.notify(mod.Notify(text="change shipped"))
                self.assertTrue(res["sent"])
                row = await db.fetchone(
                    "SELECT message_sent FROM selfevolve_runs "
                    "WHERE status = 'running'")
                self.assertEqual(int(row["message_sent"]), 1)
            self.run_async(scenario())
        finally:
            mod.httpx.AsyncClient = orig
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            os.environ.pop("TELEGRAM_CHAT_ID", None)
        self.assertEqual(len(sent), 1)
        self.assertIn("bottok/sendMessage", sent[0][0])
        self.assertEqual(sent[0][1]["text"], "change shipped")

    # ── pure logic: noise band, regression math, INCOMPLETE ───────────

    def test_regression_band_math(self):
        # tol 1.0 -> the relative part is 0: any move past the baseline
        # that clears noise/floor counts
        self.assertEqual(mod._regression_band(100.0, 1.0, None), 0.0)
        # a 1.5 tolerance on a 100 baseline allows 50 of headroom
        self.assertEqual(mod._regression_band(100.0, 1.5, None), 50.0)
        # measured noise (the 2x-stdev band from _noise_band) dominates
        self.assertEqual(mod._regression_band(100.0, 1.0, 9.0), 9.0)
        # absolute floor of 2 while the baseline is still below 5
        self.assertEqual(mod._regression_band(3.0, 1.5, 1.0), 2.0)
        self.assertTrue(mod._is_regression(10, 13, "down", 2.0))
        self.assertFalse(mod._is_regression(10, 11.9, "down", 2.0))
        self.assertTrue(mod._is_regression(10, 7.9, "up", 2.0))
        self.assertFalse(mod._is_regression(10, 8.1, "up", 2.0))
        self.assertAlmostEqual(mod._sample_stdev([1.0, 2.0, 3.0]), 1.0)
        self.assertEqual(mod._sample_stdev([5.0]), 0.0)
        self.assertEqual(mod._sample_stdev([]), 0.0)

    def test_incomplete_parsing(self):
        self.assertTrue(mod._is_incomplete(None))
        self.assertTrue(mod._is_incomplete(""))
        self.assertTrue(mod._is_incomplete("INCOMPLETE: half done"))
        self.assertTrue(mod._is_incomplete("  incomplete: retry later"))
        self.assertFalse(mod._is_incomplete("Done and verified."))
        self.assertFalse(mod._is_incomplete("Partly INCOMPLETE: no"))

    # ── friction + the shipped manifests ──────────────────────────────

    def test_friction_log(self):
        async def scenario():
            res = await mod.add_friction(mod.FrictionAdd(
                kind="correction",
                context="The user re-asked for the report in a "
                       "different format twice.",
                source="conversation"))
            self.assertTrue(res["ok"])
            rows = await mod.list_friction(limit=50)
            self.assertEqual(rows["count"], 1)
            self.assertEqual(rows["events"][0]["kind"], "correction")
        self.run_async(scenario())

    def test_manifests_match_the_brief(self):
        manifest = json.loads((_PKG / "platform/miniapps/self-evolve"
                               / ".miniapp.json").read_text())
        jobs = {j["name"]: j for j in manifest["jobs"]}
        self.assertEqual(set(jobs), {
            "self-evolve-repair-tick", "self-evolve-backlog-tick",
            "self-evolve-metrics-tick", "self-evolve-outcome-tick",
            "self-evolve-nightly", "self-evolve-meta"})
        http_targets = {jobs[n]["target"] for n in
                        ("self-evolve-repair-tick",
                         "self-evolve-backlog-tick",
                         "self-evolve-metrics-tick",
                         "self-evolve-outcome-tick")}
        self.assertEqual(http_targets,
                         {"repair/tick", "backlog/tick",
                          "metrics/sample", "metrics/reconcile"})
        self.assertEqual(jobs["self-evolve-nightly"]["cron"], "0 2 * * *")
        self.assertEqual(jobs["self-evolve-meta"]["cron"], "0 4 * * 0")
        for name in ("self-evolve-nightly", "self-evolve-meta"):
            self.assertEqual(jobs[name]["type"], "agent")
            self.assertTrue(
                jobs[name]["prompt"].startswith(
                    "First GET /api/self-evolve/settings"))
        views = {v["id"] for v in manifest["ui"]["views"]}
        self.assertEqual(views, {"overview", "backlog", "outcomes",
                                 "friction", "fitness", "runs", "repairs",
                                 "guardrails", "settings"})
        self.assertEqual(manifest["subscribes"], [])
        pkg = json.loads((_PKG / ".agentboom-package.json").read_text())
        self.assertEqual(pkg["kind"], "addon")
        self.assertEqual(pkg["requires"], ["events"])
        self.assertEqual(pkg["icon"], "\U0001F9EC")


def tearDownModule():
    # A contended asyncio.Lock binds itself to the loop that first waits
    # on it; a fresh, unbound lock restores the pristine state for the
    # rest of the suite (same as test_durable_events.tearDownModule).
    db._op_lock._lock = asyncio.Lock()


if __name__ == "__main__":
    unittest.main()
