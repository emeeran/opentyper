# OpenTyper — Phase 2 Spec

Implements the 13 MUST items from `recon.md`; assumptions per `assumptions.md`.

## User stories

1. As a user, I pick a provider model (or my custom model) in the switcher and chat with streaming responses I can stop.
2. As a user, my chats persist server-side; I rename, delete, and drag them into folders.
3. As a user, I press ⌘K and search the full text of all my messages, jumping to the chat.
4. As a user, I read markdown-formatted replies with highlighted code blocks and a per-block copy button.
5. As a user, I regenerate a bad reply to replace it.
6. As a user, I set temperature / max tokens / system prompt per chat.
7. As a user, I create agents (system prompt + base model + params) and start chats with them.
8. As a user, I keep a prompt library and insert prompts from the composer with `/`.
9. As a user, I export chats to JSON or Markdown and import them back.
10. As a user, I paste provider API keys and custom base URLs in Settings; they are stored encrypted and never returned to the browser.
11. As a user, I switch dark/light/system theme and use the UI on mobile.
12. As a user, I optionally password-protect the whole app.

## SQLite schema

File: `data/opentyper.db`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`.
IDs: UUID4 strings. Timestamps: UTC ISO-8601 text.

```sql
CREATE TABLE folders (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  provider_id   TEXT,             -- default provider for new chats in folder
  model         TEXT,             -- default model for new chats in folder
  agent_id      TEXT REFERENCES agents(id) ON DELETE SET NULL,
  system_prompt TEXT,
  position      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE chats (
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
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX idx_chats_folder ON chats(folder_id);

CREATE TABLE messages (
  id          TEXT PRIMARY KEY,
  chat_id     TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content     TEXT NOT NULL,
  model       TEXT,
  interrupted INTEGER NOT NULL DEFAULT 0,   -- stream was cancelled; partial content kept
  created_at  TEXT NOT NULL
);
CREATE INDEX idx_messages_chat ON messages(chat_id, created_at);

CREATE VIRTUAL TABLE messages_fts USING fts5(
  content, content='messages', content_rowid='rowid', tokenize='porter unicode61'
);
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content)
  VALUES ('delete', old.rowid, old.content);
END;
CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content)
  VALUES ('delete', old.rowid, old.content);
  INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TABLE providers (
  id               TEXT PRIMARY KEY,     -- builtin: 'openai','anthropic','google','openrouter'; custom: uuid
  name             TEXT NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('openai','anthropic','google')),
  base_url         TEXT,                 -- NULL = provider default; custom endpoints set this
  api_key_encrypted BLOB,                -- Fernet ciphertext; NULL = no key set
  key_hint         TEXT,                 -- masked, e.g. 'sk-…3f2a'; safe to return
  models           TEXT NOT NULL DEFAULT '[]',  -- JSON array of model ids (custom providers)
  is_builtin       INTEGER NOT NULL DEFAULT 0,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);
-- builtin rows are upserted at startup; api_key_encrypted survives re-upsert.

CREATE TABLE prompts (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  content     TEXT NOT NULL,
  tags        TEXT NOT NULL DEFAULT '[]',  -- JSON array
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE agents (
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

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,      -- theme, enter_to_send, app_password_hash, default_provider_id, default_model
  value TEXT NOT NULL
);
```

Notes: no `system` role rows stored (system prompts live on chat/agent). Search =
`messages_fts MATCH` joined back to chats for chat grouping. FTS5 external-content
table requires the three triggers above (kept in sync atomically per statement).

### Premium additions (2026-10)

`chats` gains: `tags` (JSON), `archived`, `context_limit` (history window, 0 = all),
`frequency_penalty`, `presence_penalty`, `stream` (0 = non-streamed reply),
`extra_models` (JSON `["provider:model", …]`), `use_knowledge` (0/1).
`messages` gains: `images` (JSON `[{file, mime}]` under `data/uploads/`),
`prompt_tokens`, `completion_tokens`, `cost`. Existing databases are upgraded by
idempotent `ALTER TABLE` migrations in `db.MIGRATIONS` (tested by re-running
startup after dropping columns).

```sql
CREATE TABLE documents (          -- knowledge base lite (FTS retrieval)
  id TEXT PRIMARY KEY, name TEXT NOT NULL,
  content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE documents_fts USING fts5(
  content, content='documents', content_rowid='rowid', tokenize='porter unicode61');
-- ai/ad/au triggers as for messages_fts

CREATE TABLE shares (
  token TEXT PRIMARY KEY,         -- secrets.token_urlsafe(24) capability token
  chat_id TEXT NOT NULL UNIQUE REFERENCES chats(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL
);
```

`settings` gains `model_prices` = JSON `{"model-prefix": {"in": $/Mtok, "out": $/Mtok}}`
for cost estimation (longest-prefix match; empty by default — no fabricated prices).

## Endpoints

All under `/api`, JSON. Auth middleware (401 JSON) when a password is configured.

| Method | Path | Purpose | MUST |
|---|---|---|---|
| GET | `/api/health` | liveness for systemd/nginx | — |
| POST | `/api/auth/login` `{password}` | sets HttpOnly session cookie | 13 |
| POST | `/api/auth/logout` | clears session | 13 |
| GET | `/api/models` | switcher list: builtin catalogs + custom, grouped by provider | 2 |
| GET | `/api/chats` | list (id, title, folder_id, model, pinned, tags, archived, updated_at); `?archived=true` shows archived | 3 |
| POST | `/api/chats/{id}/messages/{mid}/rerun` `{content}` | **SSE**: edit user message, drop later messages, re-stream reply | — |
| PATCH | `/api/chats/{id}/messages/{mid}` `{content}` | edit any message in place (assistant canvas-lite) | — |
| POST | `/api/chats/{id}/fork` `{message_id?}` | copy chat (optionally truncated) into a new chat | — |
| POST/DELETE | `/api/chats/{id}/share` | create/revoke secret link (`/s/{token}`) | — |
| GET | `/api/public/{token}` | read-only shared chat JSON (unauthenticated by design) | — |
| GET | `/api/uploads/{file}` | attached image (path-normalized, uploads dir only) | — |
| GET/POST | `/api/documents` · DELETE `/api/documents/{id}` | knowledge-base document CRUD | — |
| POST | `/api/chats` `{model, provider_id, folder_id?, agent_id?, system_prompt?}` | create | 3, 7 |
| GET | `/api/chats/{id}` | chat + messages | 3 |
| PATCH | `/api/chats/{id}` `{title?, folder_id?, model?, provider_id?, agent_id?, temperature?, max_tokens?, top_p?, system_prompt?, pinned?}` | rename/move/re-configure | 3, 6 |
| DELETE | `/api/chats/{id}` | delete chat + messages (cascade) | 3 |
| POST | `/api/chats/{id}/messages` `{content}` | **SSE**: persists user msg, streams assistant reply (`delta`/`done`/`error`), persists reply | 1 |
| POST | `/api/chats/{id}/regenerate` | **SSE**: drops last assistant msg, re-streams | 6 |
| GET | `/api/search?q=` | FTS5 over messages → [{chat_id, title, snippet}] | 4 |
| GET/POST | `/api/folders` · PATCH/DELETE `/api/folders/{id}` | folder CRUD | 3 |
| GET/POST | `/api/prompts` · PATCH/DELETE `/api/prompts/{id}` | prompt CRUD | 9 |
| GET/POST | `/api/agents` · PATCH/DELETE `/api/agents/{id}` | agent CRUD | 8 |
| GET/PUT | `/api/settings` `{theme, enter_to_send, default_provider_id, default_model}` | app settings (password hash never exposed) | 12 |
| PUT | `/api/settings/password` `{password}` / DELETE | set/clear app password | 13 |
| GET | `/api/providers` | list w/ key_hint, never key material | 11 |
| POST | `/api/providers` `{name, kind, base_url?}` | create custom provider | 11 |
| POST | `/api/providers/{id}/refresh-models` | fetch live `/models` list, merge into stored list | 2 |
| POST | `/api/providers/{id}/test` `{model?}` | tiny completion to verify key/endpoint/model generate a reply | 11 |
| PUT | `/api/providers/{id}` `{name?, base_url?, models?}` | custom endpoint config | 11 |
| PUT | `/api/providers/{id}/key` `{api_key}` | store Fernet-encrypted | 11 |
| POST | `/api/export` `{format: 'json'\|'markdown', chat_ids?: [...]}` | download file | 10 |
| POST | `/api/import` (JSON body = exported doc) | import chats | 10 |

**SSE contract** (`text/event-stream`):
```
event: delta   data: {"text": "…"}          // incremental assistant text
               data: {"text": "…", "model": "provider:model"}  // multi-model reply
event: meta    data: {"model": "provider:model", "message_id": "…",
                      "usage": {"prompt_tokens", "completion_tokens", "cost", "estimated"}}
event: done    data: {"interrupted": false, "message_ids": ["…"]}
event: error   data: {"message": "…scrubbed…", "model": "provider:model"?}  // stream closes
```
Client `AbortController` disconnect ⇒ server cancels upstream request, saves partial
text with `interrupted=1`, emits `done`. Usage is real when the provider reports it
(OpenAI `stream_options.include_usage`, Anthropic `message_start`/`message_delta`,
Google `usageMetadata`), else chars/4 with `estimated: true`.

## Security model

- Provider keys: Fernet-encrypted at rest in `providers.api_key_encrypted`. Key
  source: `OPENTYPER_FERNET_KEY` env, else generated `data/secret.key` (0600).
- Keys never appear in responses (only `key_hint`), logs, or error relays. Upstream
  error bodies are stripped of the key before relay; request headers are never logged.
- App password (optional): `hashlib.scrypt` (n=2^14, r=8, p=1) + salt in
  `settings.app_password_hash`; constant-time compare; session = signed HttpOnly
  SameSite=Lax cookie (itsdangerous-style HMAC with the Fernet key material — no
  extra secret). No password ⇒ no auth (default bind 127.0.0.1).
- Input validation on every write path (Pydantic models, bounds above).
- Same-origin only; no CORS middleware. Static mount serves the SPA.

## Error handling

| Case | Behavior |
|---|---|
| Provider connect/timeout (httpx, 60s connect/300s total stream) | SSE `error` with readable message; nothing persisted for assistant |
| Provider HTTP 4xx/5xx | status + scrubbed body ⇒ SSE `error`; 401/403 hint "check API key" |
| Malformed upstream stream | persist partial with `interrupted=1`, emit `error` |
| Client disconnect mid-stream | cancel upstream task, persist partial `interrupted=1` |
| Validation | 400/422 JSON `{detail}` |
| No key configured for provider | 409 `{detail: "no API key set for provider '…'"}` before any upstream call |
| Unknown chat/prompt/agent | 404 |
| Missing FTS5 at startup | fail fast with clear message (assumption A8) |

---

# Phase-2 additions (2026-10-18 parity batch)

Schema (idempotent migrations in `app/db.py`): `messages.variant_group` /
`messages.is_active` (threads-lite variants), `chats.reasoning_effort` /
`output_language` / `cache_prompt` / `temporary`, `providers.remote_models`;
new tables `memories`, `skills`, `doc_chunks`, `agents_skills_link`.

New endpoints (all under `/api`, session-guarded):

| Endpoint | Purpose |
|---|---|
| `DELETE /chats/{id}/messages/{mid}` | delete one message |
| `POST /chats/{id}/messages/{mid}/activate` | switch active reply in a variant group |
| `GET /shares` | list active share links |
| `POST /chats/{id}/finalize` | synthesize the fan-out replies of the last exchange (non-stream; persisted, tagged `finalize · <model>`) |
| `POST /side-chat` | one-shot SSE reply, nothing persisted |
| `GET/POST/PATCH/DELETE /memories` | memory CRUD (injection + auto-extraction when `memory_enabled`) |
| `GET/POST/DELETE /skills`, `POST /skills/import`, `GET/PUT /agents/{id}/skills` | SKILL.md skill packs (instructions only) |
| `GET /stats?days=` | usage dashboard aggregates |
| `POST /images/generate` | OpenAI-compatible images or Gemini image models → uploads (+ optional chat message) |
| `PATCH /documents/{id}`, `POST /documents/{id}/reindex` | document update + vector re-index |
| `POST /extract` | PDF/text extraction (pypdf, optional dep) |
| `GET /tts/elevenvoices`, `GET /tts/elevenlabs` | optional ElevenLabs voice source (encrypted key) |
| `PUT /settings/secret` | encrypted app-secret store (ElevenLabs) |

Settings keys added: `memory_enabled`, `memory_extract_prompt`,
`finalize_prompt`, `service_worker`, `embedding_provider_id`,
`embedding_model`, `shortcuts` (rebinding), `elevenlabs_hint`.
`GET /health` returns `rev` (service-worker cache key).

Security notes: skills are inlined as instructions and never executed;
memories/document content are excluded from share pages and exports of
temporary chats; DOMPurify sanitizes every markdown render path (app + share
page); provider errors remain key-scrubbed. Frontend is now native ES modules
under `static/js/` (no build step); vendored additions: DOMPurify, Mermaid +
KaTeX (lazy), pdf.js (lazy), light highlight theme.
