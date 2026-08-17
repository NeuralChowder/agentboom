-- Core scheduling tables (agentboom base schema).
-- IMMUTABLE once applied anywhere. Add your agent's own tables in new
-- migrations: 002_<concern>.sql, 003_<concern>.sql, ...

CREATE TABLE IF NOT EXISTS schedule_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'http' CHECK(type IN ('http', 'agent')),
    target TEXT,                    -- http jobs: endpoint path under /api/<app>/
    prompt TEXT,                    -- agent jobs: the prompt to run
    cron_expr TEXT,
    interval_min INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run TIMESTAMP,
    next_run TIMESTAMP,
    last_status TEXT,
    fail_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(app, name)
);
CREATE INDEX IF NOT EXISTS idx_schedule_jobs_next_run ON schedule_jobs(next_run);

CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES schedule_jobs(id),
    job_name TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    duration_ms INTEGER DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('running', 'success', 'failed')),
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_runs_job ON job_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_job_runs_started ON job_runs(started_at DESC);
