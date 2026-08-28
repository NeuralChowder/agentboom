-- Email threading metadata (agentboom package: email).
--
-- IMAP has no provider-level thread notion (unlike the Gmail API), so the
-- generic basis for grouping a conversation is the RFC 5322 threading
-- headers themselves. Capturing them at sync time means any later feature
-- (conversation view, reply-with-context) can group on real data rather
-- than guess. A message that starts its own thread has empty values.
--
-- `thread_refs` (not `references` — a reserved word) holds the References
-- header, capped; `in_reply_to` holds In-Reply-To.

ALTER TABLE emails ADD COLUMN in_reply_to TEXT;
ALTER TABLE emails ADD COLUMN thread_refs TEXT;

CREATE INDEX IF NOT EXISTS idx_emails_in_reply_to ON emails(in_reply_to);
