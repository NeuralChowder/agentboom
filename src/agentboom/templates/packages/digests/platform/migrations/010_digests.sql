-- Scheduled digests (agentboom package: digests).
-- A digest = sources + a synthesis prompt + a schedule. All three are
-- API-managed, so one engine serves any number of use cases.

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    prompt TEXT NOT NULL,
    interval_min INTEGER,               -- simple schedule
    cron_expr TEXT,                     -- takes precedence when set
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS digest_sources (
    id INTEGER PRIMARY KEY,
    digest_id INTEGER NOT NULL REFERENCES digests(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('feed', 'emails', 'endpoint')),
    ref TEXT NOT NULL,                  -- feed URL / 'limit:20' / endpoint URL
    note TEXT,
    UNIQUE(digest_id, kind, ref)
);

CREATE TABLE IF NOT EXISTS digest_runs (
    id INTEGER PRIMARY KEY,
    digest_id INTEGER NOT NULL REFERENCES digests(id) ON DELETE CASCADE,
    ran_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    content TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_digest_runs ON digest_runs(digest_id, ran_at DESC);
