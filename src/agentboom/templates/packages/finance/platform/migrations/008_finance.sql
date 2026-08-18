-- Money tracking (agentboom package: finance).
-- Use cases are defined at runtime: categories, matching rules, and
-- transactions are all API-managed resources.

CREATE TABLE IF NOT EXISTS finance_categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'expense' CHECK(kind IN ('income', 'expense')),
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS finance_rules (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category_id INTEGER REFERENCES finance_categories(id) ON DELETE SET NULL,
    match_from TEXT,          -- case-insensitive substring on sender
    match_subject TEXT,       -- case-insensitive substring on subject
    contains TEXT,            -- case-insensitive substring on body
    amount_hint REAL,         -- optional fixed amount for this rule
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS finance_transactions (
    id INTEGER PRIMARY KEY,
    category_id INTEGER REFERENCES finance_categories(id) ON DELETE SET NULL,
    rule_id INTEGER REFERENCES finance_rules(id) ON DELETE SET NULL,
    amount REAL,                            -- NULL until classified/edited
    currency TEXT NOT NULL DEFAULT 'EUR',
    direction TEXT CHECK(direction IN ('in', 'out')),
    description TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('manual', 'email')),
    email_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'confirmed', 'ignored')),
    occurred_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_finance_tx_status ON finance_transactions(status);
CREATE INDEX IF NOT EXISTS idx_finance_tx_occurred ON finance_transactions(occurred_at DESC);
