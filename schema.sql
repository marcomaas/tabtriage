CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    window_title TEXT,
    captured_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'pending'  -- pending | triaged | archived
);

CREATE TABLE IF NOT EXISTS tabs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER REFERENCES sessions(id),
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    favicon TEXT,
    summary TEXT,
    suggested_category TEXT,
    category TEXT,
    project_id TEXT,
    user_note TEXT,
    tags TEXT,
    starred INTEGER DEFAULT 0,
    cluster_id TEXT,
    cluster_label TEXT,
    og_image TEXT,
    og_description TEXT,
    captured_at TEXT DEFAULT (datetime('now')),
    triaged_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS tabs_fts USING fts5(
    title, summary, content, content=tabs, content_rowid=id
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS tabs_ai AFTER INSERT ON tabs BEGIN
    INSERT INTO tabs_fts(rowid, title, summary, content)
    VALUES (new.id, new.title, new.summary, new.content);
END;

CREATE TRIGGER IF NOT EXISTS tabs_au AFTER UPDATE ON tabs BEGIN
    INSERT INTO tabs_fts(tabs_fts, rowid, title, summary, content)
    VALUES ('delete', old.id, old.title, old.summary, old.content);
    INSERT INTO tabs_fts(rowid, title, summary, content)
    VALUES (new.id, new.title, new.summary, new.content);
END;

CREATE TRIGGER IF NOT EXISTS tabs_ad AFTER DELETE ON tabs BEGIN
    INSERT INTO tabs_fts(tabs_fts, rowid, title, summary, content)
    VALUES ('delete', old.id, old.title, old.summary, old.content);
END;
