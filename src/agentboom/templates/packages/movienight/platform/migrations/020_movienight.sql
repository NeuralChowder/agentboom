-- Movie Night (agentboom package: movienight).
-- Timestamps are ISO-8601 UTC strings written by the app (portable).
-- Booleans are INTEGER 0/1 (house style).
-- Platforms are a runtime setting — no CHECK constraint on `platform`.

-- One session per day; a same-day re-run updates the same row.
CREATE TABLE IF NOT EXISTS movienight_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_date  TEXT NOT NULL UNIQUE,           -- YYYY-MM-DD
    theme         TEXT NOT NULL,
    created_at    TEXT
);

-- The permanent catalog: one row per distinct title, ever.
-- title_fold = case- and accent-folded title (computed in Python);
-- dedup/match runs on title_fold, so a stored name keeps its accents
-- while a plain-spelled lookup still finds it.
CREATE TABLE IF NOT EXISTS movienight_titles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER REFERENCES movienight_sessions (id) ON DELETE SET NULL,
    title         TEXT NOT NULL,
    title_fold    TEXT NOT NULL,
    type          TEXT NOT NULL DEFAULT 'movie'
                   CHECK (type IN ('movie', 'series')),
    year          INTEGER,
    platform      TEXT NOT NULL,
    synopsis      TEXT NOT NULL DEFAULT '',
    poster_url    TEXT,                           -- nullable: a dead poster is normal
    why           TEXT,
    status        TEXT NOT NULL DEFAULT 'suggested'
                   CHECK (status IN ('suggested', 'watched', 'not_watched')),
    liked         INTEGER,
    comment       TEXT,
    added_at      TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_movienight_titles_title_fold
  ON movienight_titles (title_fold);
CREATE INDEX IF NOT EXISTS idx_movienight_titles_status
  ON movienight_titles (status);
CREATE INDEX IF NOT EXISTS idx_movienight_titles_session
  ON movienight_titles (session_id);

-- Feedback on titles that were never part of a session (told in chat).
CREATE TABLE IF NOT EXISTS movienight_taste_notes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    seen          INTEGER NOT NULL DEFAULT 1,
    liked         INTEGER,
    comment       TEXT,
    source        TEXT NOT NULL DEFAULT 'chat',
    noted_at      TEXT NOT NULL
);

-- Runtime settings: key/value, JSON in TEXT (house style, cf. mfa-relay).
-- Keys: platforms (TEXT JSON mapping key -> display label),
--       country (TEXT, optional; empty = "the user's local catalog").
CREATE TABLE IF NOT EXISTS movienight_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT
);
