-- Needs-attention queue (agentboom package: email-actions).

CREATE TABLE IF NOT EXISTS attention_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER NOT NULL UNIQUE REFERENCES emails(id) ON DELETE CASCADE,
    account_email TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'triaged', 'done', 'skipped')),
    needs_attention INTEGER,
    reason TEXT,
    urgency INTEGER,
    triaged_at TIMESTAMP,
    settled_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_attention_status ON attention_items(status);

CREATE TABLE IF NOT EXISTS reply_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES attention_items(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    stance TEXT,
    body TEXT NOT NULL,
    rationale TEXT,
    needs_confirmation INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_proposals_item ON reply_proposals(item_id);
