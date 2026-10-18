"""SQLite layer: schema, connections, startup init."""

import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("OPENTYPER_DATA_DIR", "data"))
DB_PATH = DATA_DIR / "opentyper.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  provider_id   TEXT,
  model         TEXT,
  agent_id      TEXT REFERENCES agents(id) ON DELETE SET NULL,
  system_prompt TEXT,
  position      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS providers (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  kind              TEXT NOT NULL CHECK (kind IN ('openai','anthropic','google')),
  base_url          TEXT,
  api_key_encrypted BLOB,
  key_hint          TEXT,
  models            TEXT NOT NULL DEFAULT '[]',
  is_builtin        INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
  id            TEXT PRIMARY KEY,
  title         TEXT NOT NULL DEFAULT 'New chat',
  folder_id     TEXT REFERENCES folders(id) ON DELETE SET NULL,
  model         TEXT NOT NULL,
  provider_id   TEXT REFERENCES providers(id) ON DELETE RESTRICT,
  agent_id      TEXT REFERENCES agents(id) ON DELETE SET NULL,
  temperature   REAL CHECK (temperature IS NULL OR (temperature >= 0 AND temperature <= 2)),
  max_tokens    INTEGER CHECK (max_tokens IS NULL OR max_tokens > 0),
  top_p         REAL CHECK (top_p IS NULL OR (top_p > 0 AND top_p <= 1)),
  system_prompt TEXT,
  pinned        INTEGER NOT NULL DEFAULT 0,
  tags          TEXT NOT NULL DEFAULT '[]',   -- JSON array of strings
  archived      INTEGER NOT NULL DEFAULT 0,
  context_limit INTEGER,                      -- history messages sent upstream; NULL = all
  frequency_penalty REAL,
  presence_penalty  REAL,
  stream        INTEGER NOT NULL DEFAULT 1,   -- 0 = single non-streamed reply
  extra_models  TEXT NOT NULL DEFAULT '[]',   -- JSON ["provider_id:model", …]
  use_knowledge INTEGER NOT NULL DEFAULT 0,   -- inject FTS snippets from documents
  reasoning_effort TEXT,                      -- NULL | none|low|medium|high|xhigh|max
  output_language  TEXT,                      -- appended to the system prompt when set
  cache_prompt  INTEGER NOT NULL DEFAULT 0,   -- mark context for provider prompt caching
  temporary     INTEGER NOT NULL DEFAULT 0,   -- temp chat: hidden from lists/search/export
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chats_folder ON chats(folder_id);

CREATE TABLE IF NOT EXISTS messages (
  id          TEXT PRIMARY KEY,
  chat_id     TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content     TEXT NOT NULL,
  model       TEXT,
  interrupted INTEGER NOT NULL DEFAULT 0,
  images      TEXT NOT NULL DEFAULT '[]',       -- JSON [{path, mime}] under data/uploads/
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  cost              REAL,
  error       TEXT,                             -- scrubbed failure reason; row kept so errors survive reload
  variant_group TEXT,                           -- replies regenerated over each other share a group
  is_active   INTEGER NOT NULL DEFAULT 1,       -- 0 = kept variant, hidden from chat + context
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content, content='messages', content_rowid='rowid', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content)
  VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content)
  VALUES ('delete', old.rowid, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TABLE IF NOT EXISTS prompts (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  content     TEXT NOT NULL,
  tags        TEXT NOT NULL DEFAULT '[]',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  description   TEXT NOT NULL DEFAULT '',
  system_prompt TEXT NOT NULL,
  model         TEXT,
  provider_id   TEXT REFERENCES providers(id) ON DELETE SET NULL,
  temperature   REAL,
  max_tokens    INTEGER,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
  content, content='documents', content_rowid='rowid', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
  INSERT INTO documents_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
  INSERT INTO documents_fts(documents_fts, rowid, content)
  VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
  INSERT INTO documents_fts(documents_fts, rowid, content)
  VALUES ('delete', old.rowid, old.content);
  INSERT INTO documents_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TABLE IF NOT EXISTS shares (
  token      TEXT PRIMARY KEY,   -- random url-safe capability token
  chat_id    TEXT NOT NULL UNIQUE REFERENCES chats(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id         TEXT PRIMARY KEY,
  content    TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT 'user',  -- user = added by hand, auto = extracted
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  content    TEXT NOT NULL,      -- SKILL.md body (instructions only, never executed)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS doc_chunks (
  id          TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  idx         INTEGER NOT NULL,
  content     TEXT NOT NULL,
  vec         TEXT NOT NULL      -- JSON float array from the embedding provider
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc ON doc_chunks(document_id);

CREATE TABLE IF NOT EXISTS agents_skills_link (
  agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  PRIMARY KEY (agent_id, skill_id)
);
"""

# Columns added after the first release: (table, column, declaration).
# CREATE TABLE IF NOT EXISTS does not touch existing databases, so upgrades
# are applied here, idempotently.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("chats", "tags", "TEXT NOT NULL DEFAULT '[]'"),
    ("chats", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("chats", "context_limit", "INTEGER"),
    ("chats", "frequency_penalty", "REAL"),
    ("chats", "presence_penalty", "REAL"),
    ("chats", "stream", "INTEGER NOT NULL DEFAULT 1"),
    ("chats", "extra_models", "TEXT NOT NULL DEFAULT '[]'"),
    ("chats", "use_knowledge", "INTEGER NOT NULL DEFAULT 0"),
    ("messages", "images", "TEXT NOT NULL DEFAULT '[]'"),
    ("messages", "prompt_tokens", "INTEGER"),
    ("messages", "completion_tokens", "INTEGER"),
    ("messages", "cost", "REAL"),
    ("messages", "error", "TEXT"),
    ("messages", "variant_group", "TEXT"),
    ("messages", "is_active", "INTEGER NOT NULL DEFAULT 1"),
    ("chats", "reasoning_effort", "TEXT"),
    ("chats", "output_language", "TEXT"),
    ("chats", "cache_prompt", "INTEGER NOT NULL DEFAULT 0"),
    ("chats", "temporary", "INTEGER NOT NULL DEFAULT 0"),
    ("providers", "remote_models", "TEXT NOT NULL DEFAULT '[]'"),
]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def check_fts5() -> None:
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    except sqlite3.OperationalError as e:  # pragma: no cover - environment guard
        raise RuntimeError("SQLite build lacks FTS5, which OpenTyper requires") from e
    finally:
        con.close()


def init_db(data_dir: Path | None = None) -> None:
    global DATA_DIR, DB_PATH
    if data_dir is not None:
        DATA_DIR = data_dir
        DB_PATH = data_dir / "opentyper.db"
    check_fts5()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "uploads").mkdir(exist_ok=True)
    con = connect()
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        con.commit()
    finally:
        con.close()
