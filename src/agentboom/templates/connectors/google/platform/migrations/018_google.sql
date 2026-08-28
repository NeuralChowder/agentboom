-- Google OAuth accounts (agentboom package: google).
--
-- Only NON-secret config lives here (which accounts are connected, their
-- scopes). The actual tokens (refresh_token / access_token) live in the
-- vault under `google:<email>` — never in this table.

CREATE TABLE IF NOT EXISTS google_accounts (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    scope TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_refresh_at TIMESTAMP
);
