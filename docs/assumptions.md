# OpenTyper — Phase 1 Assumptions

Every assumption in one place. Each item notes what changes if it is wrong.
Numbered for reference in review.

## Product scope

- **A1.** Project root is `/home/em/code/wip/opentyper/`. All paths in later docs
  are relative to it. (Wrong ⇒ move directory, no doc impact.)
- **A2.** Single-user, self-hosted instance. No multi-user accounts, no RBAC.
  Optional single password protects the whole app. (Wrong ⇒ auth model and schema
  gain a users table — significant scope change.)
- **A3.** The 9 MUST bullets in the Phase 3 brief are the contract; recon MUST tags
  group into exactly those, keeping the count ≤ 15 (recon.md lists 13 tagged groups).
  (Wrong ⇒ re-tag features in recon.md.)
- **A4.** SKIP list (plugins, MCP, RAG/Knowledge Base, cloud sync, teams, sharing
  links, chat threads/forks, tags/archive, voice/TTS, artifacts, multi-model
  responses, PWA, i18n, built-in catalogs) stays out even if cheap-looking.
  (Wrong ⇒ pull individual items into SHOULD in Phase 2.)
- **A5.** Markdown export is our own format (chat title + messages as markdown);
  original's PDF/HTML/text export formats are not replicated. (Wrong ⇒ add renderers.)

## Architecture

- **A6.** Server-side proxy of all provider calls (SSE through FastAPI), unlike the
  original's browser-direct calls. This is mandated by the brief (keys server-side,
  Fernet). (Known divergence from the original by design.)
- **A7.** Fernet key: `OPENTYPER_FERNET_KEY` env var if set, else generated at
  `data/secret.key` with 0600 perms on first run. Losing the file ⇒ stored provider
  keys unrecoverable (documented in README; backup covers it).
- **A8.** SQLite WAL, single database file under `data/`. FTS5 available in the
  system/uv Python's sqlite3 build (verify in Phase 2 gate; FTS5 is in CPython's
  bundled SQLite since 3.9-era builds — will assert at startup with a clear error).
- **A9.** Frontend is vanilla JS/CSS, no build step, vendored `marked` +
  `highlight.js` (license files kept) served from `/static`. No framework, no npm.
- **A10.** Model catalog is a small static list per provider (OpenAI, Anthropic,
  Google, OpenRouter) plus user-defined custom models (name + base URL + model id).
  No live `/models` fetching in MVP.

## Behavior

- **A11.** Chat requests: `POST /api/chat/{chat_id}/messages` returns SSE
  (`delta`/`done`/`error` events). Client abort propagates as stream close; partial
  assistant text is saved with an `interrupted` flag rather than discarded.
- **A12.** Auto title generation: one extra non-streaming call to the same
  provider/model after the first assistant reply; silently skipped on any error or
  missing key. (Wrong ⇒ drop feature to manual rename only.)
- **A13.** Timezone-free timestamps: stored as UTC ISO-8601; UI renders local.
- **A14.** Provider params passed through: temperature, max_tokens, top_p always;
  frequency/presence penalty only for OpenAI-compatible providers (Anthropic/Gemini
  reject them). Invalid combos surface upstream errors verbatim-minus-key.
- **A15.** Prompt caching, logit_bias, tool/function calling, image/file inputs are
  not sent. Upstream error messages about unsupported params are passed through.

## Security

- **A16.** Provider keys are never returned by any API (masked prefix only, e.g.
  `sk-…abc`), never logged. Upstream error bodies are scrubbed of key material
  before relay.
- **A17.** Optional password: stored as argon2/bcrypt-free stdlib hash
  (`hashlib.scrypt`), constant-time compare, HttpOnly+SameSite cookie session.
  No password configured ⇒ app binds as configured (default 127.0.0.1) with no auth.
- **A18.** Default bind is 127.0.0.1:8000; nginx example shows TLS termination for
  LAN/remote exposure. Docs warn against exposing without the password set.

## Recon fidelity

- **A19.** All TypingMind observations come from public docs/marketing pages; no
  bundle analysis, no traffic capture. Exact internal storage keys, request headers,
  and paid-tier code were intentionally not examined (clean-room + guardrail).
- **A20.** Keyboard shortcuts replicate the documented defaults (⌘K, ⌘B, ⌘⌥N,
  ⌘⌥R, ⌘⌥E, ⌘⌥C) mapped to our feature set; custom rebind UI is out of scope.

## Open questions needing your answer at Gate 1

- **Q1** (A2): password auth — cookie session OK, or prefer HTTP Basic?
- **Q2** (A10): vendor a static model list OK, or fetch `/v1/models` from providers?
- **Q3** (A5): Markdown-only export enough, or also HTML?
- **Q4** (A11): save interrupted streams as partial messages — agree?
