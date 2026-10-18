# OpenTyper — API Reference

Complete reference for the OpenTyper HTTP API (FastAPI, `app/api.py` + `app/main.py`).
For the point-and-click equivalent see `docs/user-guide.md`; for design rationale see
`docs/spec.md`.

- Base URL: `http://127.0.0.1:8000` (or your nginx origin). All JSON endpoints live
  under `/api` (router prefix, `app/api.py:22`).
- OpenAPI docs (`/docs`, `/redoc`) are **disabled** (`main.py:48`). This file is the
  reference.
- Every request/response body is JSON unless marked SSE or file.

## Authentication

| Surface | Guard |
|---|---|
| `/api/health`, `/api/auth/login` | always public |
| `/s/{token}`, `/pa/{token}`, `/api/public/{token}` | always public (token is the secret) |
| non-`/api` paths (SPA shell, static assets) | always public |
| all other `/api/*` | requires a session **only if an app password is set** |

Details (`app/main.py:139-155`):

- No password configured → every `/api` route is open (default install, bound to
  `127.0.0.1`).
- Password configured (Settings → Password) → guarded routes return
  `401 {"detail": "authentication required"}` without a valid `ot_session` cookie.
- `POST /api/auth/login` `{"password": "…"}` → `{"ok": true}` and sets `ot_session`
  (HttpOnly, SameSite=Lax, 30-day TTL, `/`). `POST /api/auth/logout` clears it.
  Sessions are HMAC-signed with key material derived from the Fernet key
  (`app/crypto.py:34,77-92`) and are invalidated by a password change.
- The SPA shell and static assets stay reachable while locked so the sign-in modal
  can render.

## Conventions

- **IDs** are UUID4 strings; timestamps are UTC ISO-8601 text.
- **Errors**: `400/404/409/422/502` with `{"detail": "…"}`. 422 = pydantic
  validation (FastAPI shape: `{"detail": [{loc, msg, type}, …]}`). 409 commonly
  means `no API key set for provider '<id>'` (`app/api.py:70`).
- **Provider errors are scrubbed**: upstream bodies are relayed with the API key
  redacted; keys are never returned by any endpoint (only `key_hint`, e.g.
  `sk-…3f2a`) and never logged (`app/providers.py:122-125`).
- **Streaming** endpoints return `text/event-stream` — see [SSE](#sse-streaming).

## SSE streaming

Four endpoints stream: `POST /api/chats/{id}/messages`, `POST
/api/chats/{id}/regenerate`, `POST /api/chats/{id}/messages/{mid}/rerun`, and
`POST /api/side-chat`. Events (`event: name\ndata: {json}\n\n`):

```
event: delta   data: {"text": "…"}                         ← per chunk
event: meta    data: {"model": "provider:model",
                      "message_id": "…",
                      "usage": {"prompt_tokens", "completion_tokens",
                                "cost", "estimated", "cache_read_tokens"?}}
event: done    data: {"interrupted": false, "message_ids": ["…"]}
event: error   data: {"message": "…scrubbed…", "model": "provider:model"?}
```

- `delta` may carry `"model"` too when a chat fans out to `extra_models` — each
  fan-out model emits its own `delta`/`meta`/`error` sequence (`app/api.py:1023-1030`).
- `usage.estimated: true` means the provider didn't report usage and tokens were
  estimated (chars/4). `cost` is present only when `model_prices` matches the model
  (longest-prefix).
- Client abort (Stop button) → upstream request cancelled, partial text persisted
  with `interrupted: 1`, stream closes with `done`.
- Provider failure mid-stream → partial text persisted with `interrupted: 1` and the
  error text; pure-error rows are excluded from later context windows.
- Non-stream chats (Stream toggle off) emit a single big `delta` instead of chunks.
- Headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no` (nginx-safe).

## Endpoints

### Health & auth

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/api/health` | — | `{"ok": true, "rev": 6}` — `rev` bumps per static change; the service worker keys its cache on it |
| POST | `/api/auth/login` | `{"password"}` | `{"ok": true}` + session cookie; 401 `invalid password` |
| POST | `/api/auth/logout` | — | `{"ok": true}` |

### Providers & models

| Method | Path | Body | Response / errors |
|---|---|---|---|
| GET | `/api/models` | — | catalog: `[{provider_id, name, kind, has_key, models, remote_count, deprecated}]` — `deprecated` lists stored ids missing from a live refresh |
| GET | `/api/providers` | — | provider rows; `api_key_encrypted` nulled, `key_hint` included |
| POST | `/api/providers` | `{name (1–100), kind: "openai"\|"anthropic"\|"google", base_url? (≤500)}` | new custom provider row |
| PUT | `/api/providers/{id}` | `{name?, base_url?, models?}` | updated row; 404 |
| PUT | `/api/providers/{id}/key` | `{api_key (1–500)}` | `{"ok": true, "key_hint"}` — stored Fernet-encrypted |
| DELETE | `/api/providers/{id}/key` | — | `{"ok": true}` — clears the key |
| POST | `/api/providers/{id}/refresh-models` | — | `{"ok": true, "fetched": n, "models": [...]}`; 409 if no key |
| POST | `/api/providers/{id}/test` | `{model?}` | `{"ok": true, "model", "latency_ms", "reply"}` or `{"ok": false, "model", "error"}`; 400 if the provider has no models yet |

### Folders

| Method | Path | Body |
|---|---|---|
| GET | `/api/folders` | — |
| POST | `/api/folders` | `{name (1–200), provider_id?, model?, agent_id?, system_prompt?}` — folder defaults apply to new chats created in it |
| PATCH | `/api/folders/{id}` | same fields |
| DELETE | `/api/folders/{id}` | — (member chats keep existing, folder nulled) |

### Chats

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/api/chats` | `?archived=true`, `?temporary=true` | pinned first, then newest activity |
| POST | `/api/chats` | `ChatCreate` (below) | defaults cascade: body → agent → folder → provider per-model-family; 400 if no provider/model resolvable |
| GET | `/api/chats/{id}` | — | chat + **all** messages (variants included, no context filtering) |
| PATCH | `/api/chats/{id}` | `ChatPatch` (below) | `""` clears `folder_id` / `agent_id` / `system_prompt` |
| DELETE | `/api/chats/{id}` | — | cascades messages, artifacts, chat share tokens |
| POST | `/api/chats/{id}/fork` | `{message_id?}` | copy (optionally truncated at a message) → `{"id", "copied_messages"}` |
| POST | `/api/chats/{id}/finalize` | `{instruction? (≤4000), provider_id?, model?}` | synthesize the last exchange's parallel replies; persisted assistant message tagged `finalize · <model>` → `{"message_id", "text", "usage"}`; 400 if no replies yet |

`ChatCreate` / `ChatPatch` fields: `model`, `title` (≤300), `provider_id`,
`folder_id`, `agent_id`, `system_prompt`, `temperature` (0–2), `max_tokens`
(≤200 000), `top_p` (0–1], `context_limit` (0–1000, 0 = all), `frequency_penalty`
/ `presence_penalty` (−2–2), `stream` (bool), `extra_models` (`["provider:model",
…]`), `use_knowledge` (bool), `reasoning_effort` (`none|low|medium|high|xhigh|max`),
`output_language` (≤60), `cache_prompt` (bool), `temporary` (create only),
`pinned` + `tags` (patch only).

### Messages & streaming

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/chats/{id}/messages` | `{content (1–200 000), images? (≤4 data URLs, ≤8 MB each, png/jpg/webp/gif), use_knowledge?}` | **SSE** |
| POST | `/api/chats/{id}/regenerate` | — | **SSE**; old reply kept as inactive variant; 400 `nothing to regenerate` |
| POST | `/api/chats/{id}/messages/{mid}/rerun` | `{content}` | **SSE**; edits the user message, re-streams; single-reply tail kept as variant, longer tail deleted; 400 if not a user message |
| PATCH | `/api/chats/{id}/messages/{mid}` | `{content}` | edit any message in place |
| DELETE | `/api/chats/{id}/messages/{mid}` | — | delete one message (variant siblings stay) |
| POST | `/api/chats/{id}/messages/{mid}/activate` | `{active: bool}` | pick the active reply in a variant group |
| POST | `/api/side-chat` | `{content (1–100 000), context? (≤100 000, truncated to 50 000), provider_id? (default "openai"), model?}` | **SSE**, one-shot, nothing persisted |

### Search, documents, extraction

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/api/search` | `?q=` (required), `?limit=50` (1–200) | FTS5 over active messages of non-temporary chats → `[{chat_id, title, updated_at, message_id, snippet}]`, snippets wrapped in `»…«` |
| GET | `/api/documents` | — | knowledge-base docs (+ `size`) |
| POST | `/api/documents` | `{name (1–300), content (1–2 000 000)}` | create |
| PATCH | `/api/documents/{id}` | `{name, content}` | update + auto re-index → `{"ok", "embedded": chunks}` |
| DELETE | `/api/documents/{id}` | — | chunks cascade |
| POST | `/api/documents/{id}/reindex` | — | chunk + embed again (no-op without embedding settings) |
| POST | `/api/extract` | `{filename, data (base64)}` | PDF/txt/md/csv/json/log → `{"text", "kind"}`; 422 details: password-protected PDF, >20 MB, scanned/image PDF, missing pypdf |

### Memories & skills

| Method | Path | Body | Notes |
|---|---|---|---|
| GET / POST | `/api/memories` | `{content (4–300)}` | manual entries get `source: "user"` |
| PATCH / DELETE | `/api/memories/{id}` | `{content}` | 404 if gone |
| GET / POST | `/api/skills` | `{name (1–200), content (1–200 000)}` | SKILL.md bodies; instructions only, never executed |
| DELETE | `/api/skills/{id}` | — | — |
| POST | `/api/skills/import` | `{filename, data (base64)}` | SKILL.md or .zip containing one; name from first `# ` heading |
| GET / PUT | `/api/agents/{id}/skills` | `{skill_ids: [...]}` | replace / list an agent's linked skills (unknown ids dropped) |

Memories are injected as system context when `memory_enabled` is on; auto-extraction
runs after each exchange (≤5 items, deduped, hard cap 200 memories).

### Agents & prompts

| Method | Path | Body |
|---|---|---|
| GET / POST | `/api/agents` | `{name (1–200), description (≤1000), system_prompt (1–100 000), model?, provider_id?, temperature? (0–2), max_tokens? (≤200 000)}` |
| PATCH / DELETE | `/api/agents/{id}` | same fields |
| GET / POST | `/api/prompts` | `{title (1–200), description (≤1000), content (1–100 000), tags: [str]}` — `{{variable}}` placeholders are filled client-side |
| PATCH / DELETE | `/api/prompts/{id}` | same fields |

### Artifacts

Versioned html/svg/markdown/code documents; every save and rewrite inserts a new
version (`root_id` groups them). Nothing is overwritten in place.

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/chats/{id}/artifacts` | `{content (1–200 000), title? (≤200), kind?: "html"\|"svg"\|"markdown"\|"code", language? (≤40), message_id?}` | creates v1 (`root_id == id`) |
| GET | `/api/chats/{id}/artifacts` | — | latest version per root + `versions` count |
| GET | `/api/artifacts/{id}` | — | one version row |
| GET | `/api/artifacts/{id}/versions` | — | chain metadata (+ `size`), no content |
| PUT | `/api/artifacts/{id}` | `{content, title?}` | **always a new version**; 409 `artifact is being edited — try again` after concurrent-save collisions |
| DELETE | `/api/artifacts/{id}` | — | deletes the whole chain + its share token → `{"ok", "deleted": n}` |
| POST | `/api/artifacts/{id}/rewrite` | `{instruction? (≤4000), provider_id?, model?}` | non-streaming rewrite via the chat's provider; fenced replies unwrapped; lands as a new version → `{"artifact", "usage"}`; 400 if content > 40 000 chars or output > 200 000 |

### Sharing

| Method | Path | Response | Notes |
|---|---|---|---|
| POST | `/api/chats/{id}/share` | `{"url": "/s/{token}"}` | idempotent; 400 for temporary chats |
| DELETE | `/api/chats/{id}/share` | `{"ok": true}` | revoke |
| GET | `/api/shares` | `[{token, chat_id, created_at, title}]` | audit list (Settings → Data) |
| GET | `/api/public/{token}` | `{"app", "title", "messages": [{role, content, model, created_at}]}` | **public by design** — title + message text only; no images, token counts, costs, variants, system prompts, memories, or documents |
| POST | `/api/artifacts/{id}/share` | `{"url": "/pa/{token}"}` | one token per artifact root; 400 for temporary chats |
| DELETE | `/api/artifacts/{id}/share` | `{"ok": true}` | revoke |
| GET | `/api/public/artifact/{token}` | `{"app", title, kind, language, content, version, created_at}` | newest version of the shared root |

Pages: `/s/{token}` renders the chat (marked + DOMPurify); `/pa/{token}` renders the
artifact (html/svg → originless `sandbox="allow-scripts"` iframe; markdown →
DOMPurify; anything else → plain `<pre>`). Deleting a chat or artifact removes its
share rows.

### Images, uploads, TTS, stats, export/import

| Method | Path | Body / query | Notes |
|---|---|---|---|
| POST | `/api/images/generate` | `{provider_id, model, prompt (1–4000), chat_id?, size? ("1024x1024")}` | OpenAI-compatible `/images/generations` or Gemini image models; stored under `data/uploads/`, optionally dropped into a chat → `{"url", "file"[, "message_id"]}` |
| GET | `/api/uploads/{filename}` | — | stored images (path-traversal-safe) |
| GET | `/api/tts/voices` | — | Edge neural catalog `[{id, name, lang, gender}]`; 502 if Microsoft's service is unreachable |
| GET | `/api/tts` | `?text= (≤5000)`, `?voice= (default en-US-AriaNeural)` | MP3 (`audio/mpeg`) via edge-tts — no key needed |
| GET | `/api/tts/elevenvoices` | — | ElevenLabs voices; 409 `no elevenlabs key configured (Settings → General)` |
| GET | `/api/tts/elevenlabs` | `?text= (≤5000)`, `?voice=` | MP3 via `eleven_multilingual_v2` |
| GET | `/api/stats` | `?days=30` (1–365) | `{"days": [{date, tokens_in, tokens_out, cost, messages}], "by_model": [...], "totals": {...}}` |
| POST | `/api/export` | `{format: "json"\|"markdown"\|"html", chat_ids?}` | file download; JSON is the re-importable full export |
| POST | `/api/import` | exported JSON doc — or ChatGPT `conversations.json` / Claude `chats.json` (auto-detected; force with `source`) | `{"ok": true, "chats": n, "folders": n, "prompts": n, "agents": n}` |

### Settings

`GET /api/settings` → all keys; `PUT /api/settings` upserts only the provided ones
(404 if `default_provider_id` / `embedding_provider_id` is unknown).

| Key | Type / default | Meaning |
|---|---|---|
| `theme` | `light\|dark\|system`, default `system` | UI theme |
| `enter_to_send` | bool, default true | Enter submits the composer |
| `default_provider_id` | str or null | provider preselected for new chats |
| `default_model` | str ≤200 or null | model preselected for new chats |
| `model_prices` | `{}` | `{"model-prefix": {"in": $/Mtok, "out": $/Mtok}}` — first matching prefix wins; empty = no cost estimates |
| `memory_enabled` | bool, default false | inject memories + background auto-extraction |
| `memory_extract_prompt` | str ≤4000 or null | overrides the built-in extraction prompt |
| `finalize_prompt` | str ≤4000 or null | overrides the built-in finalize/synthesis instruction |
| `artifact_rewrite_prompt` | str ≤4000 or null | overrides the built-in rewrite policy (instruction chips/custom text refine it; they don't replace it) |
| `service_worker` | bool, default false | opt-in offline/PWA cache (keyed to `/api/health` `rev`) |
| `embedding_provider_id` | str or null | enables vector retrieval (any OpenAI-compatible `/embeddings`) |
| `embedding_model` | str ≤200 or null | embeddings model id |
| `shortcuts` | `{}` | `{"action": "Mod+K"}` keyboard map (rebindable in Settings → Shortcuts) |

Read-only computed fields in GET: `elevenlabs_hint` (masked), `has_password` (bool).
The password hash itself is never exposed.

Secrets: `PUT /api/settings/secret` `{"name": "elevenlabs", "value": "…"}` stores the
key Fernet-encrypted; GET returns only a masked hint.

### Password

| Method | Path | Body | Notes |
|---|---|---|---|
| PUT | `/api/settings/password` | `{password (8–200)}` | scrypt-hashed (n=2¹⁴, r=8, p=1) + salt; logs you in |
| DELETE | `/api/settings/password` | — | removes the password — the app becomes open |

## Built-in providers

Upserted at startup (`app/providers.py:10-72`). Dialects: `openai` →
`POST {base}/chat/completions` (Bearer), `anthropic` → `POST {base}/v1/messages`
(`x-api-key`, `anthropic-version: 2023-06-01`), `google` →
`POST {base}/models/{model}:generateContent` (`x-goog-api-key`).

| id | Name | Dialect | Base URL | Default models |
|---|---|---|---|---|
| `openai` | OpenAI | openai | `https://api.openai.com/v1` | gpt-5, gpt-5-mini, gpt-4.1, gpt-4o, gpt-4o-mini, o3 |
| `anthropic` | Anthropic | anthropic | `https://api.anthropic.com` | claude-sonnet-4-5, claude-haiku-4-5, claude-opus-4-1 |
| `google` | Google | google | `https://generativelanguage.googleapis.com/v1beta` | gemini-2.5-pro, gemini-2.5-flash |
| `openrouter` | OpenRouter | openai | `https://openrouter.ai/api/v1` | openrouter/auto |
| `groq` | Groq | openai | `https://api.groq.com/openai/v1` | openai/gpt-oss-120b, qwen/qwen3.8-27b |
| `deepseek` | DeepSeek | openai | `https://api.deepseek.com/v1` | deepseek-chat, deepseek-reasoner |
| `mistral` | Mistral | openai | `https://api.mistral.ai/v1` | mistral-large-latest, mistral-small-latest, codestral-latest |
| `xai` | xAI | openai | `https://api.x.ai/v1` | grok-4, grok-3, grok-3-mini |
| `perplexity` | Perplexity | openai | `https://api.perplexity.ai` | sonar, sonar-pro, sonar-reasoning |
| `huggingface` | Hugging Face | openai | `https://router.huggingface.co/v1` | meta-llama/Llama-3.3-70B-Instruct, Qwen/Qwen2.5-72B-Instruct |

Any OpenAI-, Anthropic- or Google-compatible endpoint can be added as a custom
provider (name + dialect + optional base URL). `refresh-models` merges the live
catalog; stored ids missing from the live list are flagged `deprecated` (⚠ in the
picker).

Feature routing per provider: reasoning effort → OpenAI `reasoning_effort`, Anthropic
thinking budgets, Google `thinkingConfig`; `cache_prompt` → Anthropic `cache_control`
breakpoints (OpenAI/Google cache automatically); streaming usage via OpenAI
`stream_options.include_usage`, Anthropic `message_start`/`message_delta`, Google
`usageMetadata`.

Upstream timeouts: 300 s total, 60 s connect.

## Limits

| Limit | Value |
|---|---|
| Message content | 1–200 000 chars |
| Attached images | ≤4 per message, ≤8 MB each (png/jpg/webp/gif) |
| Artifact content | 1–200 000 chars; rewrite input ≤40 000 chars |
| Documents | ≤2 000 000 chars each |
| Extraction uploads | ≤20 MB; extracted text ≤2 000 000 chars |
| Skills | ≤200 000 chars each |
| Memories | 4–300 chars each, ≤200 total, ≤5 auto-extracted per exchange |
| TTS text | ≤5000 chars per request |
| Side chat | content ≤100 000; context truncated to 50 000 |
| Context window | `context_limit` 0–1000 messages (0/null = all) |
| Vector chunks | 1200 chars, 150 overlap, cosine cutoff 0.15, top-k 3 |
| Search results | 1–200 (default 50) |
| Stats window | 1–365 days (default 30) |

## Environment variables

| Var | Read by | Effect |
|---|---|---|
| `OPENTYPER_DATA_DIR` | `app/main.py`, `app/db.py` | data directory (default `data/`): `opentyper.db`, `secret.key`, `uploads/` |
| `OPENTYPER_FERNET_KEY` | `app/crypto.py` | Fernet key material; wins over auto-generated `data/secret.key`. Also derives the session-signing key. **Back this up** — losing it means stored provider keys can't be decrypted |
| `OPENTYPER_HOST` / `OPENTYPER_PORT` | systemd unit only (`deploy/`) | bind address for uvicorn (defaults `127.0.0.1:8000`); set in `/etc/opentyper/opentyper.env` (deb) |
| `OPENTYPER_BACKUP_DIR` | `deploy/backup.sh` | backup destination (default `/var/backups/opentyper`) |

## Database layout

SQLite (WAL, foreign keys on) at `data/opentyper.db` — `app/db.py:10-206`:

| Table | Purpose |
|---|---|
| `folders` | sidebar folders; may pre-set provider/model/agent/system prompt |
| `providers` | providers + Fernet `api_key_encrypted`, `key_hint`, JSON `models` |
| `chats` | conversations incl. all sampling params, tags, `extra_models`, flags |
| `messages` | turns; image refs, token counts + cost, `error`, variant fields |
| `messages_fts` | FTS5 index over message content (triggers keep it in sync) |
| `prompts` | prompt templates |
| `agents` | personas (system prompt + optional model/provider/params) |
| `settings` | key/value: settings, password hash, encrypted secrets |
| `documents` / `documents_fts` | knowledge base + FTS index |
| `doc_chunks` | per-document embedding chunks (JSON float arrays) |
| `shares` | chat share capability tokens |
| `memories` | long-term facts (`source`: user \| auto) |
| `skills` / `agents_skills_link` | SKILL.md bodies + agent↔skill links |
| `artifacts` | versioned artifacts (`root_id`, `UNIQUE(root_id, version)`) |
| `artifact_shares` | artifact share tokens (one per root) |

Column migrations are idempotent `ALTER TABLE` entries in `db.MIGRATIONS`; new
tables live in `SCHEMA`. Startup fails fast if the SQLite build lacks FTS5.

## Security notes

- Provider keys and app secrets (ElevenLabs) are Fernet-encrypted at rest; the API
  only ever returns masked hints.
- Upstream error bodies are key-scrubbed before relay; request headers are never
  logged.
- Share URLs are capability tokens (`secrets.token_urlsafe(24)`), revocable, and the
  public payloads are minimal (see Sharing above). Memory/document/skill/provider
  data is never exposed on a share page.
- All rendered markdown goes through DOMPurify (app + share pages); html/svg
  artifacts render only in an originless `sandbox="allow-scripts"` iframe.
- Skills are inlined as instructions and never executed.
- No CORS middleware; same-origin only.
