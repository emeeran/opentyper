# OpenTyper — User Guide

A task-oriented manual for everyday use. For installation and server administration
see the [README](../README.md); for the HTTP API see [api.md](api.md); for what's
implemented vs. TypingMind see [parity.md](parity.md).

OpenTyper is a self-hosted chat UI for LLMs: your chats live in a SQLite database on
your own server, and your provider API keys are stored encrypted there too. The
browser never sees a key; the server talks to the providers.

---

## 1. Getting started

1. Open OpenTyper in a browser (`http://127.0.0.1:8000` by default).
2. **Settings → Providers** → pick a provider card, paste its API key, **Save key**.
   Test with **⚡ Test** (shows the model's reply and latency). Optional: **⤓ Fetch
   latest models** to get the provider's live catalog, or **⤓ Import keys from
   .env** — the file is parsed in your browser and never uploaded; keys go to the
   server only through the provider cards.
3. Pick a model in the header switcher, type in the composer, press **Enter**.
4. Optionally set an app password (**Settings → Password**) — without one, anyone
   who can reach the server can read your chats.

## 2. Interface tour

| Region | What's there |
|---|---|
| **Sidebar** | New chat, Search (⌘K), Show archived, the chat list grouped under folders, then Prompts, Agents, Documents, Memory, Usage, Theme, Settings |
| **Chat header** | Sidebar toggle, editable title, **model switcher**, gear (chat settings popover), Fork, Regenerate, Finalize (only when extra models are set), Artifacts, Delete chat |
| **Message area** | The conversation, a scroll-to-bottom button when scrolled up, and "Load earlier" when a chat has more than 200 messages |
| **Composer** | Attach, Generate image (wand), Voice input (mic), the text area with the `/` prompt menu, context meter, full-screen editor, Send/Stop |
| **Floating** | Selection chips ("Ask…", "Search web") when you select message text; TTS player bar while speech plays; toasts top-right; offline banner |

## 3. Chatting

- **Send**: Enter sends, Shift+Enter adds a newline. With "Enter to send" off
  (Settings → General), Ctrl/⌘+Enter sends. While a reply streams, Send becomes
  **Stop**.
- **Streaming**: replies arrive token-by-token and render progressively; the full
  markdown (code highlighting, tables, diagrams, math) renders when done.
- **Drafts**: whatever you've typed is saved per chat and restored when you come
  back; cleared on send.
- **Context meter**: shows `~N.Nk tok` when the conversation is large enough for it
  to matter (rough estimate).
- **Full-screen editor**: the expand button in the composer or **Ctrl/⌘+Shift+F** —
  a distraction-free writing surface for long prompts.
- **Interrupted streams**: pressing Stop keeps the partial reply, marked
  *interrupted*; you can regenerate later.
- **Notifications**: if a reply finishes while the tab is hidden you'll hear a soft
  chime (and it will be spoken aloud if auto-speak is on).

### Attachments

The paperclip (or drag-and-drop onto the composer) accepts:

| Type | What happens |
|---|---|
| Images (png/jpg/webp/gif, ≤4 per message, ≤8 MB each) | sent as native vision inputs; models that support images will see them |
| Text/code files ≤ 512 KB | contents inlined into your message as a fenced block |
| PDFs ≤ 20 MB | text extracted server-side and attached as `<name>.txt` (needs the `pypdf` extra) |
| Anything else | skipped with a toast |

Click an attachment chip or a message attachment to preview it (image viewer, PDF
viewer for the first 20 pages, or text).

### Prompt library and `/`

Type `/` in the composer to open the prompt menu (also works when the composer
isn't focused). It lists your saved prompts with previews — ↑/↓ and Enter to pick.
Prompts with `{{variable}}` placeholders open a fill-in dialog first. Create and edit
prompts under **Prompts** in the sidebar.

### Voice input

The mic button uses the browser's speech recognition (Chrome/Edge). Interim words
appear as you speak; the transcript is appended to whatever's in the composer.

### Ask about a selection (side chat)

Select more than a couple of characters in any message: two chips appear — **Ask…**
opens a floating side-chat panel (with its own model picker) that answers about the
selection, and **Search web** opens DuckDuckGo for it in a new tab. Side chats are
never persisted.

## 4. Reading and replying to messages

Each message has:

- **Header**: "You" or the model name (with a `provider:model` badge), plus a
  timestamp.
- **Actions**: Copy on everything; **Speak** (text-to-speech) on replies; **Edit**
  on both; **Branch** on your own messages; **Delete** on both.
- **Usage line** on replies: `~⇅ 1.2k tok (cached) · $0.003` — `~` means tokens were
  estimated, "cached" is the cache-read count when the provider reports one, cost
  shows only if you've configured [model prices](#18-usage-and-costs).
- **Variants switcher** `‹ 2/3 ›`: appears when a message has multiple versions
  (from regenerate or edit-&-re-run) — flip through them; the shown one is what the
  model sees as context going forward.
- **Retry** appears on failed replies.

Editing behaviors:

- **Edit your message → Save & re-run**: the reply regenerates from that point. The
  old reply is kept as a variant (nothing is destroyed).
- **Edit a reply**: changes it in place.
- **Regenerate** (header button or Ctrl/⌘+Alt+E): re-streams the last reply; the old
  one becomes an inactive variant.
- **Branch** on a user message: copies the chat up to that message into a new chat.
- Old messages: only the most recent 200 render — "Load N earlier message(s)"
  expands the rest.

## 5. Models and providers

- **Switcher**: the header dropdown groups models by provider; providers without a
  key are suffixed "(no key)". Models that disappeared from a provider's live
  catalog are flagged **⚠** (possibly deprecated).
- **Custom providers**: Settings → Providers → **＋ Add custom provider** — name, API
  kind (`openai`, `anthropic`, or `google`), and base URL. Anything
  OpenAI-compatible works (OpenRouter, Groq, DeepSeek, LM Studio, Ollama's
  OpenAI endpoint, vLLM…).
- **Per provider**: save/remove the key, set the base URL, edit the model list,
  fetch the live list, test.

## 6. Chat settings (the gear popover)

Every chat carries its own configuration:

| Setting | Notes |
|---|---|
| Temperature, Max tokens, Top P | standard sampling controls |
| Context limit | how many recent messages are sent upstream (0 = all) |
| Frequency / presence penalty | −2 to 2 |
| Reasoning effort | Model default / Off / Low / Medium / High / Extra high / Max — mapped to each provider's native knob |
| Output language | e.g. "Spanish" — appended to the system prompt |
| Stream responses | off = wait for the full reply |
| Use documents (knowledge) | pull context from your [knowledge base](#12-documents-knowledge-base) |
| Prompt caching | Anthropic cache breakpoints (OpenAI/Google cache automatically) |
| **Temporary chat** | one-off chat, deleted when you switch away (see §9) |
| Tags | comma-separated; shown as tooltips on chat list items |
| **Extra models** | `provider:model, …` — enables [multi-model fan-out](#10-multi-model-and-finalize) |
| System prompt | the chat's standing instructions |

Bottom row: **Save**, **Archive/Unarchive**, **Pin/Unpin**, **Share link**.

## 7. Organizing chats

- **Folders**: the folder icon next to "Chats" creates one. A folder can carry
  default provider/model/system prompt/agent for new chats created in it (the `+` on
  a folder header). **Drag a chat onto a folder header** to move it. Deleting a
  folder keeps its chats.
- **Bulk actions**: the check icon next to "Chats" enters selection mode — Move…,
  Archive, Delete many at once.
- **Pin**: pinned chats float to the top of the list (params popover → Pin).
- **Archive**: hides from the list; toggle "Show archived" to see them.
- **Rename**: click the title in the chat header. New chats are auto-titled after
  the first exchange (see §8).
- **Search**: ⌘K / Ctrl+K (or the sidebar button) full-text searches all active
  messages of non-temporary chats; results show snippets and open the chat scrolled
  to the hit.
- **Fork**: the header button copies the whole chat (Branch copies from a specific
  message).
- **Delete**: header trash — also revokes the chat's share link. Confirm dialog.

## 8. Auto-titling and memory

A new chat is titled "New chat" until the first exchange completes, then a background
call proposes a title (best-effort; failures leave the default).

**Memory** (sidebar): turn on "Remember facts about me across chats" and OpenTyper
extracts durable facts after each reply (max 5 per exchange, deduped, capped at 200
total), editable in the panel — automatically-learned entries are labeled as such.
When enabled, memories are injected into every chat as a system context line.
Nothing from memory ever appears on share pages.

## 9. Temporary chats

Tick **Temporary chat** in the params popover: the chat is hidden from lists,
search, export, and can't be shared. A banner warns it's "deleted when you switch
away" with a **Keep** button to convert it into a normal chat. Leaving without
keeping discards it.

## 10. Multi-model and finalize

Add entries to **Extra models** (e.g. `anthropic:claude-sonnet-4-5`) in the params
popover, and each message fans out to your main model plus every extra model — you
get parallel reply bubbles. A **✦ Finalize** button then appears in the header: it
sends all the replies to a model for synthesis into a single answer, persisted as a
`finalize · model` message. The synthesis instruction is editable in Settings →
Prompts.

## 11. Agents and skills

**Agents** (sidebar): reusable personas — system prompt plus optional model,
provider, temperature, and max tokens. New chats can be created from an agent (it
fills the defaults), and folders can point at an agent too.

**Skills**: import a `SKILL.md` (or a `.zip` containing one) in the Agents modal and
attach skill packs to agents. Skills are *instructions* — they're inlined into the
system prompt and never executed.

## 12. Documents (knowledge base)

**Documents** (sidebar): upload text files (.txt, .md, .csv, .json, code files; ≤2 MB
each) to build a local knowledge base. Chats with **Use documents** enabled pull the
most relevant snippets into context as a system message before the model replies.

Retrieval is hybrid: keyword (full-text) search always runs; if you configure an
embeddings provider + model in Settings → General, embedding similarity is merged
in for better matches. Retrieval failures never block a send.

## 13. Artifacts

Turn any code block into a live, versioned document:

- Click **Artifact** on any code block in a reply except Mermaid diagrams (or the
  header's Artifacts button to see everything this chat has saved). Identical blocks
  are deduped — you won't get two copies of the same artifact.
- The docked panel has three tabs: **Preview** (html/svg render in a sandboxed
  frame, markdown rendered, code as text), **Code** (editable), **Versions** (the
  full history).
- **Save version** commits your edits as a new version — nothing is ever
  overwritten. The header's `‹ n/m ›` flips through versions.
- **AI rewrite**: preset chips — **Shorter**, **Longer**, **Fix bugs**, **Polish** —
  or your own instruction. The rewritten result lands as a new version. Rewriting is
  text editing only; it never executes anything.
- Copy, Download (`title.ext`), and Delete (whole version chain) live in the footer.
- Unsaved Code-tab edits trigger a "Discard unsaved changes?" guard.
- Artifacts can be [shared](#16-sharing) via a public read-only link.

## 14. Image generation

The composer's wand button opens a dialog: prompt, model (`provider:model`, default
`openai:gpt-image-1`), optional size. The image is generated through your configured
provider, stored on the server, and dropped into the chat as a message.

## 15. Speech

**Text-to-speech** — the Speak button on any reply, or "Speak replies automatically"
in Settings. Three engines (Settings → General):

| Engine | Needs | Notes |
|---|---|---|
| Browser voices | nothing (a voice must exist) | availability varies; on Linux usually needs speech-dispatcher |
| Edge neural voices | nothing — server-side | large natural voice catalog; server must reach Microsoft's read-aloud service |
| ElevenLabs | an API key (stored encrypted) | paste it in Settings → General, then pick a voice |

The player bar (above the composer) has play/pause, stop, seek, and a time readout.
Rate, pitch, and volume sliders apply. Text is capped at 5000 characters per
request. If server audio fails, playback falls back to the browser voice.

**Voice input**: the composer mic (see §3).

## 16. Sharing

- **Chat**: params popover → **Share link** → a secret URL (`/s/…`) anyone can read,
  no login. The dialog offers Copy and **Revoke access**; all active links are
  listed in Settings → Data. Shared pages expose **only the title and message
  text** — not images, usage, system prompt, memories, or documents.
- **Artifact**: the artifacts panel has a share action → `/pa/…` renders the latest
  version (html/svg in a sandboxed frame, markdown sanitized, code as text).
- Links are capability tokens: whoever has the URL can read until you revoke.
  Deleting a chat or artifact also removes its link. Temporary chats can't be
  shared.

## 17. Import and export

Settings → Data:

- **Export**: JSON (complete, re-importable), Markdown, or HTML (print to PDF).
- **Import**: accepts an OpenTyper JSON export — or a ChatGPT `conversations.json`
  / Claude `chats.json` export, auto-detected.

Server-level backups (`deploy/backup.sh`) are separate — see the README.

## 18. Usage and costs

**Usage** (sidebar): a 30-day daily chart plus a per-model table — tokens in/out,
estimated cost, message counts. `/api/stats?days=N` can widen the window (up to a
year).

Costs need prices: Settings → General → **Model prices (JSON)**, e.g.

```json
{"gpt-4o": {"in": 2.5, "out": 10}, "claude-sonnet": {"in": 3, "out": 15}}
```

Prefix match (longest wins), USD per million tokens. Empty = no cost estimates
shown anywhere.

## 19. Settings reference

| Tab | What's there |
|---|---|
| **General** | theme, Enter-to-send, wide layout, offline cache (service worker); TTS engine + voice pickers + rate/pitch/volume + auto-speak + test; model prices; embeddings provider/model |
| **Providers** | per-provider key, base URL, model list, fetch-models, test; custom provider creation; .env key import |
| **Shortcuts** | rebind the six global shortcuts (see §20); conflicts are prevented; reset to defaults |
| **Prompts** | the three editable internal prompts: finalize synthesis, memory extraction, artifact rewrite |
| **Data** | exports, import, active share links with revoke |
| **Password** | set or clear the app password (min 8 chars) |

## 20. Keyboard shortcuts

Default bindings — **Mod** means Ctrl (Linux/Windows) or ⌘ (macOS). Rebindable in
Settings → Shortcuts; the `?` overlay always reflects your current bindings.

| Action | Default | Does |
|---|---|---|
| Search | Mod+K | full-text search |
| Toggle sidebar | Mod+B | show/hide the sidebar |
| New chat | Mod+Alt+N | — |
| Regenerate | Mod+Alt+E | re-stream the last reply |
| Copy last reply | Mod+Alt+C | — |
| Shortcuts overlay | `?` | — |

Fixed (not rebindable): `/` prompt menu · Mod+Shift+F full-screen composer ·
Enter send / Shift+Enter newline · Escape closes panels, popovers, and dialogs.

## 21. Appearance and display

- **Theme**: the sidebar Theme button cycles **system → light → dark**; System
  follows your OS. Code highlighting, Mermaid diagrams, and the browser UI color all
  follow the resolved theme.
- **Wide layout** (Settings → General) widens the message column.
- The layout adapts down to phone widths; the sidebar becomes a drawer.

## 22. Offline cache (PWA)

Settings → General → "Offline cache (service worker)" installs a cache that lets the
UI shell load without a network. It is opt-in, the API is always network-only (never
served stale), and the cache is invalidated automatically whenever the server's
static assets change. Typical use: install the app to a phone/tablet home screen.

## 23. Privacy and security

- Provider keys are encrypted at rest (Fernet) and never returned to the browser —
  only masked hints like `sk-…3f2a`.
- The optional app password is scrypt-hashed; sessions are signed, HttpOnly cookies
  valid 30 days.
- Share pages are the only unauthenticated data surfaces, and only expose chat
  text/artifact content, revocable at any time.
- Memory, documents, skills, and provider configuration never leak into shares,
  exports of temporary chats, or error messages.
- Skills and AI rewrites never execute code.
- With no password set the app is open — keep it bound to localhost or put it behind
  nginx with authentication. The default install binds `127.0.0.1`.

## 24. Troubleshooting

| Symptom | Fix |
|---|---|
| "no API key set for provider …" | add the key in Settings → Providers, then ⚡ Test |
| Model picker shows "(no key)" | the provider has no key yet — new chats fall back to one that does |
| Streaming stalls behind nginx | ensure `proxy_buffering off` and a long `proxy_read_timeout` (`deploy/nginx.conf.example`) |
| Reply failed with an auth error | the provider rejected the key — re-check it in Settings → Providers |
| Edge TTS fails | the server can't reach Microsoft's read-aloud service; fall back to browser voices |
| Browser voices missing on Linux | install speech-dispatcher, or use Edge voices |
| PDF attach fails on scanned files | scanned PDFs have no text layer; run OCR first |
| Search finds nothing | only active messages of non-temporary chats are indexed |
| Forgot the app password | `sqlite3 data/opentyper.db "DELETE FROM settings WHERE key='app_password_hash'"` then restart |
| Lost `secret.key` | stored provider keys can't be decrypted — remove and re-enter them |

## 25. Where everything lives

Server side (all inside `data/` unless configured otherwise): `opentyper.db`
(chats, messages, documents, memories, artifacts, settings…), `secret.key`
(encryption key — **back this up**), `uploads/` (attached and generated images).
Export from Settings → Data for a portable copy; `deploy/backup.sh` for full
server backups.
