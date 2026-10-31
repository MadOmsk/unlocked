-- Short links (#244): a code for each thing of the catalog shared. A target is
-- a key that outlives a rebuild of the catalog, never a version's or a game's
-- id. In the shared catalog, so one thing has one code on every server.
CREATE TABLE IF NOT EXISTS games.short_links (
    code       TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    target     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (kind, target)
);
