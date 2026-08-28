-- GitHub repo watching (agentboom package: github-watch).

CREATE TABLE IF NOT EXISTS watched_repos (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL UNIQUE,          -- 'org/name'
    enabled INTEGER NOT NULL DEFAULT 1,
    last_checked_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS github_events (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('issue', 'release')),
    ref TEXT NOT NULL,                  -- 'issue-123' / 'release-v1.2.3'
    title TEXT,
    url TEXT,
    actor TEXT,
    github_created_at TEXT,
    seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo, kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_github_events_seen ON github_events(seen_at DESC);
