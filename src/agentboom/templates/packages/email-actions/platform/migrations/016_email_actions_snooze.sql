-- Snooze — the fourth outcome for mail that needs the user, but not yet.
--
-- The queue had three fates for a message: handle it, mark it done, or skip
-- it. Real days have a fourth — "important, not now" — and without a place
-- for it such mail either squats in the queue burying what CAN be handled
-- now, or gets skipped and quietly forgotten.
--
-- `snoozed_until` parks the item: while it is in the future the open queue
-- does not return the row, and when it passes the row returns by itself —
-- the query condition flips, so there is no restore job to miss. A NULL
-- snooze is ordinary queue behaviour, which is also why nothing about
-- existing rows changes.
--
-- Applied once (tracked in _migrations), so the plain ADD COLUMN is safe on
-- both SQLite and PostgreSQL.

ALTER TABLE attention_items ADD COLUMN snoozed_until TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_attention_snoozed
    ON attention_items(snoozed_until);
