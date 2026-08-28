-- MFA relay (agentboom package: mfa-relay).
-- Timestamps are ISO-8601 UTC strings written by the app (portable).

CREATE TABLE IF NOT EXISTS mfa_relay_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS mfa_relay_forwards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id INTEGER NOT NULL UNIQUE,
  account_email TEXT,
  from_email TEXT,
  code TEXT,
  recipient TEXT,
  action TEXT NOT NULL,
  reason TEXT,
  created_at TEXT,
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mfa_relay_forwards_created
  ON mfa_relay_forwards (created_at);
