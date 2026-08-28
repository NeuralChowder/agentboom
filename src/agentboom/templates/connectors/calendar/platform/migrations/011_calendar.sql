-- Calendar accounts + event cache (agentboom package: calendar).
-- Passwords live in the vault under 'calendar:<account-id>'.

CREATE TABLE IF NOT EXISTS cal_accounts (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL UNIQUE,
    username TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'caldav',
    caldav_url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cal_events (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES cal_accounts(id) ON DELETE CASCADE,
    uid TEXT NOT NULL,
    summary TEXT NOT NULL,
    start_at TIMESTAMP,
    end_at TIMESTAMP,
    all_day INTEGER NOT NULL DEFAULT 0,
    location TEXT,
    description TEXT,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, uid)
);
CREATE INDEX IF NOT EXISTS idx_cal_events_start ON cal_events(start_at);
