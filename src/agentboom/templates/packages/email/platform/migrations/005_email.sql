-- Email foundation (agentboom package: email).
-- Mailbox passwords are NOT stored here — they live in the vault under
-- the service name 'email:<address>'.

CREATE TABLE IF NOT EXISTS email_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'imap',
    imap_host TEXT NOT NULL,
    imap_port INTEGER NOT NULL DEFAULT 993,
    smtp_host TEXT,
    smtp_port INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_sync_at TIMESTAMP,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES email_accounts(id) ON DELETE CASCADE,
    folder TEXT NOT NULL DEFAULT 'INBOX',
    uid TEXT NOT NULL,
    message_id TEXT,
    from_email TEXT,
    from_name TEXT,
    subject TEXT,
    received_at TIMESTAMP,
    has_attachment INTEGER NOT NULL DEFAULT 0,
    body_text TEXT,
    UNIQUE(account_id, folder, uid)
);
CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_emails_from ON emails(from_email);

CREATE TABLE IF NOT EXISTS email_filters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    match_from TEXT,          -- case-insensitive substring on sender
    match_subject TEXT,       -- case-insensitive substring on subject
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- The receipts: a filter with no record of what it dropped is
-- indistinguishable from a bug. Subject + sender only — the message
-- itself was the thing not kept.
CREATE TABLE IF NOT EXISTS email_filter_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filter_name TEXT NOT NULL,
    account_email TEXT NOT NULL,
    from_email TEXT,
    subject TEXT,
    skipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_filter_log_at ON email_filter_log(skipped_at DESC);
