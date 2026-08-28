-- Movie Night (agentboom package: movienight) — PostgreSQL variant.
-- Same portable column types as the SQLite base; only the identity
-- columns differ.

CREATE TABLE IF NOT EXISTS movienight_sessions (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_date  TEXT NOT NULL UNIQUE,           -- YYYY-MM-DD
    theme         TEXT NOT NULL,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS movienight_titles (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id    BIGINT REFERENCES movienight_sessions (id) ON DELETE SET NULL,
    title         TEXT NOT NULL,
    title_fold    TEXT NOT NULL,
    type          TEXT NOT NULL DEFAULT 'movie'
                   CHECK (type IN ('movie', 'series')),
    year          INTEGER,
    platform      TEXT NOT NULL,
    synopsis      TEXT NOT NULL DEFAULT '',
    poster_url    TEXT,
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

CREATE TABLE IF NOT EXISTS movienight_taste_notes (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title         TEXT NOT NULL,
    seen          INTEGER NOT NULL DEFAULT 1,
    liked         INTEGER,
    comment       TEXT,
    source        TEXT NOT NULL DEFAULT 'chat',
    noted_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movienight_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT
);
