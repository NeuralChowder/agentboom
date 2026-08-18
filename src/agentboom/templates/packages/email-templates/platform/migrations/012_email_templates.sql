-- Email templates (agentboom package: email-templates).
--
-- Per-mailbox HTML wrappers for outgoing mail. The message body goes into
-- the wrapper's {{body}} slot; {{footer}} renders the footer line.
--
--   * email_templates — the designs. scope_email NULL means shared: any
--     mailbox may activate it, so one seasonal design (say, Christmas)
--     serves every mailbox without being copied per account. Names are
--     unique PER SCOPE (COALESCE(scope_email,'*'), name), so a personal
--     and a shared template may both be called "Christmas".
--   * email_template_active — each mailbox's current choice. A missing row
--     means "the default". The built-in default is also seeded as a shared
--     row flagged is_fallback, so it can be listed, previewed and reworded;
--     the code constant remains only as the absolute last resort.

CREATE TABLE IF NOT EXISTS email_templates (
    id INTEGER PRIMARY KEY,
    scope_email TEXT,                 -- NULL = shared across all mailboxes
    name TEXT NOT NULL,
    html TEXT NOT NULL,
    description TEXT,
    season TEXT,                      -- e.g. 'christmas', 'summer', NULL
    is_fallback INTEGER NOT NULL DEFAULT 0,   -- the built-in default row
    created_by TEXT,                  -- 'agent' | 'user'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_tpl_scope_name
    ON email_templates(COALESCE(scope_email, '*'), name);

CREATE TABLE IF NOT EXISTS email_template_active (
    account_email TEXT PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES email_templates(id) ON DELETE CASCADE,
    activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
