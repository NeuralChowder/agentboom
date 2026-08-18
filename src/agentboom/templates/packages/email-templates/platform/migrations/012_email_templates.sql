-- Email templates (agentboom package: email-templates).
-- The template library + which template each mailbox currently uses.
-- scope_email NULL on a template means it is shared ("All mailboxes").

CREATE TABLE IF NOT EXISTS email_templates (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    scope_email TEXT,                 -- NULL = shared across all mailboxes
    html TEXT NOT NULL,
    description TEXT,
    season TEXT,                      -- e.g. 'christmas', 'summer', NULL
    created_by TEXT,                  -- 'agent' | 'user'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_template_active (
    account_email TEXT PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES email_templates(id) ON DELETE CASCADE,
    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
