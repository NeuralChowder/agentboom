-- Email threading metadata (agentboom package: email) — PostgreSQL variant.

ALTER TABLE emails ADD COLUMN IF NOT EXISTS in_reply_to TEXT;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS thread_refs TEXT;

CREATE INDEX IF NOT EXISTS idx_emails_in_reply_to ON emails(in_reply_to);
