# OpenTyper — Phase 1 Recon: TypingMind (clean-room)

Date: 2026-10-17
Target: https://www.typingmind.com (public site) + https://docs.typingmind.com (public docs)

## Method & evidence note

- Observation source: publicly accessible pages only — marketing homepage, official
  documentation (feature list, general settings, keyboard shortcuts, chat management,
  project folders, prompt library, AI agents, export/import, general FAQs, static
  self-host FAQs), and the docs `llms.txt` index (~220 pages).
- No REA MCP server is connected in this session; the `rea` CLI exists
  (`~/.npm-global/bin/rea`) but no applicable REA route was used: no HAR capture, no
  local app bundle/artifact was downloaded, and no browser runtime was attached.
  All findings below are **fetch-based observations of public docs** (labeled per
  source URL), not packet captures or runtime traces. Network-call patterns are
  taken from TypingMind's own FAQ statements, not verified by interception.
- Nothing proprietary was copied: feature names are referenced as behavior
  descriptions; no source, assets, branding, or copy text is reproduced.
- Deliberately not done: downloading/analyzing the minified app bundle. It would add
  bundle-internal detail (exact localStorage keys, exact request headers) that a
  clean-room clone does not need and must not copy. If deeper confirmation is ever
  required: save the app's JS bundle locally and run
  `rea analyze-javascript-application <dir> --json`.

## What the original is (architecture)

TypingMind is a **static web app with no backend** (their FAQ, general-faqs.md):
- All data (chats, prompts, agents, settings) lives in browser localStorage.
- API keys are stored locally in the browser; optional AES encryption with a
  user password.
- Chat requests go **directly from the browser to the provider** (OpenAI,
  Anthropic, Google, etc.). License validation and optional Cloud Sync hit
  TypingMind's own servers (cloud.typingmind.com / license server).

**OpenTyper diverges here by explicit requirement** (this is our design choice, not
observed behavior): server-side SQLite (WAL), provider keys stored server-side
encrypted with Fernet, and streaming proxied through FastAPI via SSE. Everything
else in this recon describes the original's observable product surface.

## Feature inventory

Tags: MUST = in OpenTyper MVP scope · SHOULD = cheap, add if trivial · SKIP = out of scope.

### Models & providers
| Feature | Tag | Notes |
|---|---|---|
| Multiple providers via API keys (OpenAI, Anthropic, Google Gemini, OpenRouter, DeepInfra, DeepSeek, Groq, Mistral, Moonshot, Perplexity, xAI, Z.ai) | MUST | We implement OpenAI-compatible + Anthropic + Google contracts; long tail reachable via custom base URL |
| Custom models on OpenAI-compatible endpoints (LMStudio, Ollama, LocalAI, Azure-style) | MUST | Custom base URL + model id |
| Model switcher per chat | MUST | |
| Stream on/off toggle | MUST | SSE default |
| Temperature, top_p, max tokens, presence/frequency penalty | MUST (first three) / SHOULD (penalties) | Per-chat params |
| Context limit (number of recent messages sent) | SHOULD | Trivial: slice message list |
| Prompt caching toggles per provider | SKIP | Provider-specific extras |
| Custom endpoint / proxy | MUST | Same mechanism as custom base URL |
| Multi-model responses in one conversation | SKIP | |
| Cost/token estimation | SHOULD | Rough chars/4 estimate only, clearly labeled estimate |

### Chat experience
| Feature | Tag | Notes |
|---|---|---|
| Streaming chat with stop | MUST | Cancelled streams handled server-side |
| Markdown + code blocks, copy button, syntax highlighting | MUST | Vendored lib, no CDN |
| Enter-to-send vs Cmd/Ctrl+Enter (setting) | SHOULD | |
| Auto-generate chat title | SHOULD | Cheap second call after first exchange |
| Rename chat | MUST | |
| Delete chat / message edit-regenerate | MUST (chat delete + regenerate); edit-message SHOULD | |
| Folders (drag chats in, new chat inside folder, move, bulk select) | MUST | |
| Project folders (folder-level model/agent/system instructions) | SHOULD | Folder default model + system instruction only |
| Pin chats | SHOULD | |
| Tags on chats | SKIP | |
| Archive / auto-archive / auto-delete archived | SKIP | |
| Full-text chat search | MUST | SQLite FTS5 |
| Fork chat / chat threads (versions per regeneration) | SKIP | Linear history + regenerate-in-place only |
| Canvas editor, side chat (ask/quote), drafts, floating selection toolbar | SKIP | |
| Share chat via secret link | SKIP | No public sharing in v1; export covers the need |
| Voice input, text-to-speech, sound notifications | SKIP | |
| Documents/video upload, RAG / Knowledge Base, Dynamic Context | SKIP | |
| Artifacts, plugins (web search, image gen, HTTP/JS/MCP), user JS extensions | SKIP | Explicitly out of scope |
| Parallel multi-conversations | SKIP | Single active chat view |
| Slash menu ("/" in composer → prompts/agents/settings) | SHOULD | Prompt insertion only |

### Library
| Feature | Tag | Notes |
|---|---|---|
| Prompt library (CRUD, tags) | MUST | |
| Prompt templates with `{{variables}}` | SHOULD | Fill-in dialog on use |
| Built-in prompt/agent catalogs (160+ prompts, 50+ agents) | SKIP | Ship a handful of local examples, no bundled catalog |
| AI Agents (system prompt + base model + params) | MUST | |
| Agent extras: few-shot examples, welcome message, conversation starters, version history, share links, pin | SKIP | |

### Data & settings
| Feature | Tag | Notes |
|---|---|---|
| Export chats (JSON), per-chat export | MUST | |
| Export as Markdown | MUST | Our format; original exports JSON/PDF/HTML/text |
| Import chats (JSON) | MUST | |
| Import from ChatGPT/Claude exports | SKIP | |
| Settings: provider API keys + custom base URLs | MUST | Server-side, Fernet-encrypted at rest |
| Settings: theme (light/dark/system) | MUST | |
| Settings: keyboard shortcuts (view defaults; custom rebind) | MUST (defaults) / SKIP (rebind UI) | |
| Settings: general toggles (stream, enter-to-send, reopen last chat, lazy rendering, queued delay) | SHOULD | Stream + enter-to-send only |
| Multiple profiles | SKIP | |
| Cloud sync, teams, licensing, PWA install, i18n/translation | SKIP | Self-hosted single-user |
| Optional app password | SHOULD | Single optional password per target spec |

### Keyboard shortcuts (documented defaults; ⌘=Ctrl on Windows/Linux)
| Action | Keys | OpenTyper |
|---|---|---|
| Open search | ⌘K | MUST |
| Toggle sidebar | ⌘B | MUST |
| New chat | ⌘⌥N | MUST |
| Reset chat | ⌘⌥R | SHOULD |
| Regenerate last response | ⌘⌥E | SHOULD |
| Clear context | ⌘⌥J | SKIP |
| Toggle plugins | ⌘⌥P | n/a |
| Copy last message | ⌘⌥C | SHOULD |
| Focus composer | (ours) / | SHOULD |

### Definitive OpenTyper MUST list (Gate 1 count = 13 < 15)

1. Multi-provider streaming chat (SSE) with stop/cancel
2. Model switcher: static catalogs + custom OpenAI-compatible models (name, base URL, model id)
3. Chat history: create / rename / delete, folders with move
4. Full-text search over chats and messages (SQLite FTS5)
5. Markdown + code blocks: copy button, syntax highlighting, vendored locally
6. Regenerate last response
7. Per-chat settings: temperature, max tokens, system prompt (top_p passed through)
8. Agents: named system prompt + base model + params, selectable per chat
9. Prompt library: CRUD + tags, insert into composer
10. Import/export chats: JSON + Markdown
11. Settings UI: provider API keys (Fernet-encrypted at rest) + custom base URLs
12. Dark/light/system theme, responsive layout
13. Keyboard shortcuts ⌘K / ⌘B / ⌘⌥N (plus optional single app password)

## UI component map (original layout, as documented)

```
┌─────────────┬──────────────────────────────────────────────┐
│ Left        │ Top bar: chat title · model switcher · info  │
│ sidebar     ├──────────────────────────────────────────────┤
│             │ Message list (user/assistant bubbles,        │
│ + New chat  │   markdown, code blocks w/ copy, cost badge) │
│ Search      │                                              │
│ Folders     ├──────────────────────────────────────────────┤
│  └ chats    │ Composer: "/" slash menu · params popover ·  │
│ Pin chats   │   send · stop · cost/context indicator       │
│ Workspace   ├──────────────────────────────────────────────┤
│ bar: Models │ Modals: Settings (General / Appearance /     │
│ Prompts /   │   Keyboard / App Data & Storage / API Keys / │
│ Agents / KB │   Models) · Prompt Library · Agents · Search │
└─────────────┴──────────────────────────────────────────────┘
```

- Sidebar: new chat, search entry, folder tree (+ per-folder new chat, 3-dot menu:
  rename/project settings), chat rows (title, star pin, 3-dot: rename/export/move/
  delete), bulk select via Shift+Click.
- Workspace bar: Models (provider list + API keys tab + add custom model),
  Prompt Library (browse/add/`{{vars}}`), Agents (create: instructions, base model
  & params, knowledge, starters).
- Settings modal tabs (documented): General, Keyboard Shortcuts, Customize
  Appearance, App Data & Storage (export/import/archived), API Keys, Text-to-Speech,
  Proxy.
- Chat header: title, model selector, info ("About this chat": regenerate title,
  message/token counts).
- Composer: text area, prompt "/" menu, output-settings popover (temperature, max
  tokens, system instruction, context limit), send/stop.

## Inferred data model (original, localStorage; reconstructed from docs)

- `chats`: id, title (auto-generated, editable), messages[], model, parameters,
  folderId, pinned, tags[], archivedAt, threads/forks, drafts.
- `messages`: id, role (user/assistant/system), content (markdown), model, tokens/
  cost estimate, createdAt, variants (thread versions).
- `folders`: id, name, projectSettings {model, agentId, systemInstructions, files}.
- `prompts`: id, title, description, content, tags[], templateVariables[].
- `agents`: id, name, instructions, baseModel, params, fewShotExamples[],
  welcomeMessage, conversationStarters[], knowledgeRefs[], pinned, versions[].
- `settings`: apiKeys{provider→(optionally AES-encrypted) key}, customModels[],
  general flags (streaming, enterToSend, autoArchive, rendering…), theme,
  keyboardShortcuts{}, profile(s) {avatar, instructions}.
- Export JSON = selectable subset of the above collections.

OpenTyper equivalent (server-side): same entities in SQLite; keys in a
`providers` table storing Fernet ciphertext only.

## Provider API contracts (public, standard — used by OpenTyper server-side)

| Provider | Endpoint | Auth | Streaming |
|---|---|---|---|
| OpenAI (+ any OpenAI-compatible custom base URL, OpenRouter, Groq, DeepSeek, Mistral, xAI, LMStudio, Ollama…) | `POST {base}/v1/chat/completions` | `Authorization: Bearer <key>` | SSE, `data: {...}` lines, `data: [DONE]` |
| Anthropic | `POST https://api.anthropic.com/v1/messages` | `x-api-key` + `anthropic-version` header | SSE events: `message_start`, `content_block_delta`, `message_stop` |
| Google Gemini | `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent` | `x-goog-api-key` header (or `?key=`) | SSE with `alt=sse` |
| OpenRouter | OpenAI-compatible, `https://openrouter.ai/api/v1` | Bearer | same as OpenAI |

OpenTyper exposes one internal contract regardless of provider: `POST /api/chat`
→ SSE frames (`delta`, `done`, `error`), client never sees provider keys.

## Open questions

1. Confirm MUST list (below) matches your intent — Gate 1.
2. Optional single password: session cookie vs HTTP Basic? (Assumed: cookie session,
   password hash in env/config; no password ⇒ open access on localhost.)
3. Markdown/highlight renderer: vendored `marked` + `highlight.js` assumed — any
   size constraint against this?
4. Auto title generation uses which provider/model? (Assumed: the chat's own model,
   skipped if no key configured.)
5. Import format: only OpenTyper JSON, or also best-effort TypingMind-style JSON?
   (Assumed: OpenTyper JSON v1 only; structure documented in spec.)
