-- Durable event bus (agentboom package: events) — PostgreSQL variant.
-- Same portable column types as the SQLite base; only the identity
-- columns differ.

CREATE TABLE IF NOT EXISTS events_log (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  type TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'platform',
  subject TEXT,
  payload TEXT NOT NULL DEFAULT '{}',
  dedupe_key TEXT UNIQUE,
  published_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_log_published ON events_log (published_at);

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
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id BIGINT NOT NULL REFERENCES events_log(id),
  app_name TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  max_retries INTEGER NOT NULL DEFAULT 5,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'delivering', 'delivered', 'failed', 'dead')),
  attempts INTEGER NOT NULL DEFAULT 0,
  next_retry_at TEXT,
  last_error TEXT,
  response TEXT,
  delivered_at TEXT,
  created_at TEXT,
  UNIQUE (event_id, app_name, endpoint)
);
CREATE INDEX IF NOT EXISTS idx_events_deliveries_due
  ON events_deliveries (status, next_retry_at);
