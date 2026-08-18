-- Knowledge graph (agentboom package: brain).

CREATE TABLE IF NOT EXISTS brain_entities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'topic',     -- person | company | topic | ...
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS brain_observations (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES brain_entities(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    source TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_brain_obs_entity ON brain_observations(entity_id);

CREATE TABLE IF NOT EXISTS brain_mentions (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES brain_entities(id) ON DELETE CASCADE,
    email_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_brain_mentions_entity ON brain_mentions(entity_id);
