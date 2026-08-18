-- Document collections (agentboom package: documents).
-- Collections and their matching rules are API-managed: one engine,
-- any number of use cases (invoices, receipts, warranties, contracts).

CREATE TABLE IF NOT EXISTS document_collections (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    match_from TEXT,          -- case-insensitive substring on sender
    match_subject TEXT,       -- case-insensitive substring on subject
    note TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL
        REFERENCES document_collections(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'manual' CHECK(source IN ('email', 'manual')),
    email_id INTEGER,                       -- set when source = 'email'
    title TEXT NOT NULL,
    notes TEXT,
    file_name TEXT,                         -- optional: a storage package file
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(collection_id, email_id)
);
CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection_id);
