-- Durable knowledge notes (agentboom package: knowledge).

CREATE TABLE IF NOT EXISTS knowledge_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',      -- comma-separated, lowercase
    source TEXT,                        -- where the fact came from
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_knowledge_updated ON knowledge_notes(updated_at DESC);
