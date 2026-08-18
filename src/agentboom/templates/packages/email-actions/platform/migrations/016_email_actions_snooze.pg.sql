-- Snooze (agentboom package: email-actions) — PostgreSQL variant.

ALTER TABLE attention_items ADD COLUMN IF NOT EXISTS snoozed_until TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_attention_snoozed
    ON attention_items(snoozed_until);
