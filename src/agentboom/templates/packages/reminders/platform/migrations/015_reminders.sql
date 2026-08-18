-- Reminders (agentboom package: reminders).

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_at TIMESTAMP NOT NULL,
    recurrence TEXT,                 -- NULL | 'daily' | 'weekly'
    delivered INTEGER NOT NULL DEFAULT 0,
    delivered_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(due_at);
