# OpenTyper

Self-hosted bring-your-own-API-key chat UI for LLMs. FastAPI + SQLite backend,
vanilla JS frontend, SSE streaming, provider keys encrypted at rest. Clean-room
alternative to TypingMind — see `docs/parity.md` for what matches, what doesn't.

**Highlights:** multi-provider streaming chat (OpenAI/Anthropic/Google/
OpenRouter/Groq/DeepSeek/Mistral/xAI/Perplexity/HuggingFace + custom
OpenAI-compatible endpoints), folders/tags/archive/pin, full-text search,
agents + prompt library + SKILL.md skills, edit & re-run / fork with reply
**variants**, image inputs (vision), file attach (incl. PDF text extraction),
documents knowledge base (hybrid FTS + vector retrieval), **multi-model
fan-out with a finalize/synthesis step**, **memory**, **side chat**,
**temporary chats**, reasoning-effort + prompt-caching controls, output
language, Mermaid + LaTeX + table rendering, image generation, per-chat params
(temperature, penalties, context window, stream toggle), token/cost estimates
+ usage dashboard, secret share links (revocable, manageable), JSON/Markdown/
HTML export + import (incl. ChatGPT/Claude exports), text-to-speech (browser,
Edge neural, or ElevenLabs), voice input, drafts, **artifacts (docked editor
with version history and AI rewrite, shareable)**, a
sanitized (DOMPurify) accessible UI with dark/light/system themes and
rebindable shortcuts, optional app password, opt-in offline cache.

```
opentyper/
├── app/            backend (main.py, api.py, db.py, crypto.py, providers.py,
│                   tts.py, vectors.py, extract.py)
├── static/         SPA (index.html, js/* ES modules, style.css, vendored
│                   marked/highlight/DOMPurify + lazy mermaid/katex/pdf.js)
├── tests/          pytest suite + mock provider
├── scripts/        demo.py (SSE smoke) · browser_check.py (full UI pass, headless
│                   Chrome) · live_smoke.py (real-provider smoke)
├── deploy/         install.sh, build_deb.sh, nginx.conf.example, backup.sh
└── docs/           recon + recon-premium, assumptions, spec, parity,
                     improve_plan (executed), TODO (checklist)
```

## Requirements

- Linux (tested: Ubuntu 26.04 LTS x86_64), [uv](https://docs.astral.sh/uv/)
- Outbound HTTPS to your AI providers
- Optional: nginx for TLS, sqlite3 CLI for backups

## Run (development)

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Then:

1. **Settings → Providers** — paste an API key for OpenAI / Anthropic / Google /
   OpenRouter, or add a custom provider (OpenAI-compatible base URL).
   You can also import keys from a `.env` file there.
2. Pick a model in the header, type, send. Replies stream.

Verify everything end-to-end without any provider account:

```bash
uv run pytest tests/ -q                                  # 97 tests
uv run scripts/demo.py                                   # app + mock provider, streams a chat
uv run --with playwright python scripts/browser_check.py # 71 UI checks in headless Chrome
```

The browser pass drives the real SPA (stream, stop, regenerate, edit & re-run,
fork, share, archive, attachments, knowledge injection, fan-out, TTS bar,
auth, theme) against a scripted mock provider. It uses your system Chrome
(`/usr/bin/google-chrome`); no browser download.

## Install (production, Ubuntu 26.04)

**Option A — deb package** (self-contained, offline target, clean uninstall):

```bash
./deploy/build_deb.sh                  # → dist/opentyper_<version>_amd64.deb
sudo dpkg -i dist/opentyper_*_amd64.deb
```

The package ships the source plus its own Python runtime and venv, so the
target needs no network, no uv, and no system Python. It installs to
`/opt/opentyper`, enables a hardened systemd service on `127.0.0.1:8000`,
and health-checks it. Port or address conflicts (another app on 8000) are
solved by setting `OPENTYPER_PORT` / `OPENTYPER_HOST` in
`/etc/opentyper/opentyper.env` and restarting the service. Upgrades
(`dpkg -i` the new deb) restart the service and never touch
`/opt/opentyper/data`; `/etc/opentyper/opentyper.env` is a conffile for
overrides. Requires `uv` + `dpkg-deb` on the build machine only.

**Option B — install script:**

```bash
sudo ./deploy/install.sh --dry-run   # review first — changes nothing
sudo ./deploy/install.sh             # installs to /opt/opentyper, runs via systemd
```

The installer: installs uv if missing, creates the `opentyper` system user,
syncs the source to `/opt/opentyper`, creates `data/` (0700) and
`/var/log/opentyper`, writes a hardened systemd unit (`NoNewPrivileges`,
`ProtectSystem=strict`, `PrivateTmp`, more), enables and starts the service,
then health-checks `http://127.0.0.1:8000/api/health`.

Service management:

```bash
systemctl status opentyper
journalctl -u opentyper -f
sudo systemctl edit opentyper   # or set OPENTYPER_PORT / OPENTYPER_HOST below

# Change the port/address — add to /etc/opentyper/opentyper.env (deb) and
# restart; the same vars work in the Environment= of a hand-written unit:
#   OPENTYPER_PORT=8010
#   OPENTYPER_HOST=127.0.0.1
```

## Configure

All state lives in `data/` (default `<app dir>/data`; override with
`OPENTYPER_DATA_DIR`):

| File | Purpose |
|---|---|
| `opentyper.db` | SQLite (WAL): chats, messages, folders, prompts, agents, documents, shares, settings |
| `secret.key` | Fernet key for provider-key encryption (auto-created, 0600) |
| `uploads/` | Attached images, stored server-side and sent as native image parts |

- **Provider keys**: set in the UI (Settings → Providers). Stored only as
  Fernet ciphertext in the DB; the API returns masked hints, never keys.
- **Fernet key source**: `OPENTYPER_FERNET_KEY` env var wins; otherwise
  `data/secret.key` is generated. **Back it up** — without it, stored provider
  keys cannot be decrypted (chats remain readable).
- **App password** (optional): Settings → Password. Min 8 chars, scrypt-hashed.
  The SPA shell stays reachable so the sign-in screen can render; all `/api`
  routes require the session. Share links (`/s/{token}`) bypass it by design —
  revoke after use. Without a password the app is open — keep it bound to
  localhost or behind nginx with auth.
- **Text-to-speech**: Settings → General picks browser voices (Web Speech API)
  or Edge neural voices (server-side `edge-tts`, no key needed — the server
  must reach Microsoft's read-aloud service). Voice input uses the browser's
  Web Speech recognition where available.
- **TLS / remote access**: put nginx in front using `deploy/nginx.conf.example`
  (SSE-safe: `proxy_buffering off`, long read timeout) and certbot for certs.

## Upgrade

```bash
sudo ./deploy/install.sh        # idempotent; rsyncs source, keeps data/ intact
sudo systemctl restart opentyper
```

The installer never touches `data/`, and recreates the venv only if its
interpreter moved out of the app dir.

## Backup

```bash
sudo ./deploy/backup.sh --dry-run     # preview
sudo ./deploy/backup.sh               # online .backup + integrity check + restore test
sudo ./deploy/backup.sh --keep 14     # keep 14 rotated copies
```

Backups land in `/var/backups/opentyper/` (override `OPENTYPER_BACKUP_DIR`),
0600, rotated, each restore-tested by replay into a scratch DB. Schedule it:

```
17 3 * * * /opt/opentyper/deploy/backup.sh >> /var/log/opentyper/backup.log 2>&1
```

For user-facing data export, use the UI (Settings → Data): JSON (full,
re-importable), Markdown, or single-chat HTML (printable to PDF).
`secret.key` is **not** in chat backups — copy it separately if you need
provider keys to survive a restore.

## Troubleshoot

| Symptom | Fix |
|---|---|
| `service not active` in installer | `journalctl -u opentyper -n 50` — usually port already bound or `data/` perms |
| 409 "no API key set" | add the key in Settings → Providers |
| SSE reply never appears behind nginx | ensure `proxy_buffering off` (see nginx example) |
| `SQLite build lacks FTS5` at startup | your Python's SQLite lacks FTS5; use uv-managed Python (default) |
| lost `secret.key` | provider keys must be re-entered; delete rows via Settings → Providers → Remove key, re-add |
| forgot app password | `sqlite3 data/opentyper.db "DELETE FROM settings WHERE key='app_password_hash'"` then restart |
| stream cut at exactly 60s behind proxy | raise `proxy_read_timeout` (example file uses 360s) |

## Status

See `docs/parity.md` for the feature parity table. Verified by 97 pytest
endpoint tests, the SSE demo, and a 71-check automated browser pass
(`scripts/browser_check.py`). Still recommended before real use: one manual
look on a phone (responsive layout is CSS-only) and a live-provider smoke
test with a real key (open items in parity.md → UNRESOLVED).
