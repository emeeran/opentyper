# Recon: TypingMind premium features (public sources only)

Sources: docs.typingmind.com `/feature-list`, `/llms.txt` index,
`/quickstart/typingmind-license-plans` (fetched 2026-10-17). No auth bypassed,
no paid-tier code or assets inspected — feature behavior only, from public docs.

OpenTyper already covers the "Standard" core (multi-provider chat, folders,
pin, agents, prompts, params, upload-docs partially, import/export, BYOK keys
encrypted, custom endpoints). This doc inventories the rest and decides what
OpenTyper integrates.

## BUILD (this iteration)

| # | TypingMind feature | OpenTyper implementation (original) |
|---|---|---|
| 1 | Edit & fork conversations | Edit a sent message → later messages dropped → reply re-streams. Fork chat copies messages (optionally up to a message) into a new chat. Canvas-lite: edit an assistant reply in place. |
| 2 | Chat Thread (versions) | Not a tree — edit+regenerate truncates (standard behavior). Full version trees SKIPped. |
| 3 | Multi-model responses | `extra_models` on a chat: one send fans out to N extra models in parallel; replies appear as separate tagged assistant bubbles. |
| 4 | Vision (images in prompts) | Attach images client-side (data URL → stored under `data/uploads/`); sent as provider-native image parts (OpenAI `image_url`, Anthropic `image.source.base64`, Google `inlineData`). |
| 5 | Upload documents | Text-ish files (.txt/.md/.csv/.json/.code) are read client-side and inlined into the message as fenced context. No PDF parsing (SKIP). |
| 6 | Knowledge Base (RAG) | Lite, no embeddings: documents library + FTS5; when a chat has "use knowledge" on, top snippet matches are injected as context. Real vector RAG SKIPped. |
| 7 | Context limit | Per-chat `context_limit` = number of history messages sent upstream (0 = all). |
| 8 | Presence/frequency penalty | Per-chat params, passed to OpenAI-compatible APIs; ignored by anthropic/google kinds (their APIs differ). |
| 9 | Stream response control | Per-chat `stream` toggle — off = single non-streamed reply. |
| 10 | API tokens & cost estimation | Real `usage` parsed from provider streams (Anthropic always; OpenAI when it sends it; Google `usageMetadata`), char/4 estimate as fallback. Per-model prices editable in Settings (JSON, $/Mtok); cost shown only when a price is configured — we do not ship possibly-wrong price data. |
| 11 | Chat tags | `tags` JSON on chats; chips in sidebar; click-to-filter. |
| 12 | Archive chats | `archived` flag; hidden from sidebar unless "show archived"; delete stays explicit. |
| 13 | Share chat (secret link) | `shares` table with random token; public read-only page `/s/{token}` + JSON `/api/public/{token}`; bypasses the app password by design (link-holder only). Revocable. |
| 14 | Export to HTML/PDF | Single-chat HTML export (self-contained, printable to PDF from the browser) alongside existing JSON/Markdown. |
| 15 | Voice input | Web Speech API (`SpeechRecognition`) mic button — browser-provided, no server code. |
| 16 | Text-to-speech | Web Speech API `speechSynthesis` per assistant reply. ElevenLabs SKIPped (third-party key + paid service). |
| 17 | Sound notification | Short WebAudio chime when a reply finishes while the tab is hidden. |
| 18 | Save draft | Per-chat composer draft in `localStorage`, restored on open, cleared on send. |
| 19 | Artifacts (lite) | "Preview" button on ```html code blocks → sandboxed `<iframe srcdoc>` modal. No in-iframe editing loop. |
| 20 | Prompt template & variables | Already present (`{{var}}` fill-in); kept. |
| 21 | Hotkeys overlay | `?` opens a shortcuts cheat-sheet modal. |
| 22 | Wide screen support | `wide` layout toggle (localStorage). |
| 23 | PWA | `manifest.webmanifest` + original SVG icon (installability depends on browser; no service worker — offline caching SKIPped to avoid stale-asset bugs). |
| 24 | Pre-fill chat via URL | `/?q=...` seeds the composer. |

## SKIP (with reason)

| Feature | Why |
|---|---|
| Plugins framework (HTTP/JS actions), MCP servers | Arbitrary client/server code execution + large surface; out of scope for a single-user self-hosted tool. Alternatives: custom OpenAI-compatible providers, Perplexity `sonar` models for web-grounded answers. |
| DALL-E / image generation | Provider-specific images API, separate cost/UX surface; not requested yet. |
| Cloud Sync (TypingCloud) | N/A — OpenTyper is already server-side; any device hitting the URL shares state. |
| Teams: SSO/SCIM/roles/analytics/branding/usage limits | Multi-user admin product; OpenTyper is deliberately single-user (one optional password). |
| Extensions (custom JS injection) | Same class as plugins: arbitrary code, no safe sandbox worth shipping. |
| Profiles (multiple key sets) | Single-user; providers already swappable per chat. |
| Video upload (Gemini) | Large-binary upload/storage pipeline for one provider. |
| Prompt caching toggle | Anthropic `cache_control` helps only long-system-prompt repeat traffic; the OpenAI/Google paths cache implicitly. Not observable-requestable without live keys; SKIP until asked. |
| Full version trees / side-chat / canvas co-editing loop | Complexity vs. value for one user; edit+rerun + fork covers the workflow. |
| Search keyword suggestions / CodePen button | Marginal; composer copy button already covers the main path. |
| ElevenLabs TTS | Paid third-party service; browser TTS shipped instead. |
| PDF/video parsing in uploads | Vendoring a PDF parser for a text-inline feature; paste-the-text works today. |

## Legal note

Everything here is built from public marketing/docs descriptions of behavior.
No TypingMind source, assets, icons, branding, or copy text was read or reused;
all UI, names, and code are original. Premium-*tiers* are a licensing concept —
behavior parity is not a license violation; code/asset copying would be.

## Assumptions

- One user, one browser — localStorage is acceptable for UI prefs (wide mode, drafts).
- Usage numbers are best-effort: providers that don't return usage in streams get
  a chars/4 estimate; the UI labels estimates as estimates.
- Share links are capability URLs: whoever holds the token reads the chat.
  They bypass the app password on purpose (that's what "share" means here) and
  can be revoked.
