-- Self-evolve (agentboom package: self-evolve) — PostgreSQL variant.
-- Same portable column types as the SQLite base; only the identity
-- columns differ.

CREATE TABLE IF NOT EXISTS selfevolve_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS selfevolve_runs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  trigger TEXT NOT NULL DEFAULT 'schedule',
  status TEXT NOT NULL DEFAULT 'running'
    CHECK (status IN ('running', 'done', 'failed')),
  started_at TEXT,
  finished_at TEXT,
  findings TEXT,
  changes TEXT,
  message_sent INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_selfevolve_runs_started
  ON selfevolve_runs (started_at);

CREATE TABLE IF NOT EXISTS selfevolve_backlog (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  title TEXT NOT NULL,
  why TEXT NOT NULL,
  tier TEXT NOT NULL DEFAULT 'proposal'
    CHECK (tier IN ('autonomous', 'proposal', 'deferred')),
  evidence TEXT,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'in-flight', 'adopted', 'dismissed')),
  resolution TEXT,
  drain_attempts INTEGER NOT NULL DEFAULT 0,
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_selfevolve_backlog_status
  ON selfevolve_backlog (status, updated_at);
CREATE INDEX IF NOT EXISTS idx_selfevolve_backlog_title
  ON selfevolve_backlog (lower(title));

CREATE TABLE IF NOT EXISTS selfevolve_repair_requests (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind TEXT NOT NULL,
  target_id BIGINT NOT NULL DEFAULT 0,
  fingerprint TEXT NOT NULL,
  error TEXT,
  state TEXT NOT NULL DEFAULT 'requested'
    CHECK (state IN ('requested', 'in-flight', 'resolved', 'expected', 'escalated')),
  count INTEGER NOT NULL DEFAULT 1,
  attempts INTEGER NOT NULL DEFAULT 0,
  result TEXT,
  backlog_id BIGINT,
  lease_until TEXT,
  first_seen TEXT,
  last_seen TEXT,
  updated_at TEXT,
  UNIQUE (kind, target_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_selfevolve_repair_state
  ON selfevolve_repair_requests (state);

CREATE TABLE IF NOT EXISTS selfevolve_metrics (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name TEXT NOT NULL,
  value DOUBLE PRECISION NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}',
  sampled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_selfevolve_metrics_name
  ON selfevolve_metrics (name, sampled_at);

CREATE TABLE IF NOT EXISTS selfevolve_friction (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind TEXT NOT NULL,
  context TEXT NOT NULL,
  source TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_selfevolve_friction_created
  ON selfevolve_friction (created_at);

CREATE TABLE IF NOT EXISTS selfevolve_change_outcomes (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id BIGINT,
  genome_commit TEXT,
  change_summary TEXT NOT NULL,
  metric_name TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('up', 'down')),
  baseline_value DOUBLE PRECISION NOT NULL,
  measured_value DOUBLE PRECISION,
  reverted_commit TEXT,
  verdict TEXT NOT NULL DEFAULT 'pending'
    CHECK (verdict IN ('pending', 'kept', 'regressed', 'reverted', 'confounded')),
  confounders TEXT,
  decided_at TEXT,
  note TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_selfevolve_outcomes_verdict
  ON selfevolve_change_outcomes (verdict);

CREATE TABLE IF NOT EXISTS selfevolve_guardrail_alerts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  metric_name TEXT NOT NULL,
  window_days INTEGER NOT NULL,
  from_value DOUBLE PRECISION NOT NULL,
  to_value DOUBLE PRECISION NOT NULL,
  threshold DOUBLE PRECISION,
  changes_in_window TEXT,
  raised_at TEXT,
  acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_selfevolve_alerts_raised
  ON selfevolve_guardrail_alerts (raised_at);
