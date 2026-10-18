# OpenTyper vs TypingMind — Feature Parity

*Updated 2026-10-18 after the parity implementation batch (improve_plan.md /
TODO.md). Baseline was the MVP parity audit; this version reflects TypingMind
Premium's feature set as of the October 2026 changelog.*

Legend: **done** = implemented + tested · **partial** = implemented, gaps noted ·
**skipped** = out of scope (reason) · **UNRESOLVED** = not working / unverified.

## Core chat

| Feature | Status | Notes |
|---|---|---|
| Multi-provider chat (10 built-in + custom) | **done** | server-side proxy, keys encrypted at rest; live smoke green for google/groq/openrouter (UNRESOLVED #2) |
| Model switcher per chat | **done** | static catalogs + custom models; deprecated ids flagged ⚠ after a live refresh |
| Streaming (SSE) with stop | **done** | abort persists partial with `interrupted`; mid-stream render throttled (no per-delta markdown/highlight) |
| Markdown + code copy + highlighting | **done** | marked + highlight.js, **sanitized with DOMPurify** (app + share page); light+dark highlight themes |
| Regenerate | **done** | old reply kept as an **inactive variant** (threads-lite) with a ‹ 1/2 › switcher |
| Auto chat title | **done** | best-effort background call |
| Temperature / max tokens / top_p / penalties / context limit / stream toggle | **done** | per chat |
| **Reasoning effort** (none→max) | **done** | OpenAI `reasoning_effort`, Anthropic thinking budgets (temp/top_p dropped as required), Google `thinkingConfig` |
| **Prompt caching** | **done** | Anthropic `cache_control` on system + tail (opt-in per chat); OpenAI/Gemini automatic; cached-token counts surfaced in usage when reported |
| **Output language** | **done** | appended to system prompt per chat |

## Organization

| Feature | Status | Notes |
|---|---|---|
| Chat history CRUD, folders (+defaults), tags, archive | **done** | |
| **Pin chats** | **done** | toggle in the params popover (was API-only) |
| **Folder drag-and-drop** | **done** | drag chats onto folder headers; bulk select mode (move/archive/delete) |
| Full-text search | **done** | FTS5, snippets, active messages only; results open the chat **scrolled to the hit** |
| Edit & re-run / edit assistant reply | **done** | single-reply tails keep the old reply as a variant; longer tails truncate |
| Fork chat | **done** | full or truncated at a message; "Branch" action per user message |
| Chat threads (version trees) | **partial** | threads-lite: variants on regenerate/rerun with per-message switcher; no n-ary trees/rebase (deliberate) |
| Per-chat bulk actions | **done** | selection mode in sidebar (was skipped at MVP scale) |

## Library

| Feature | Status | Notes |
|---|---|---|
| Prompt library CRUD + `{{variable}}` fill-in | **done** | variables fill via an in-app dialog (no native `prompt()`) |
| `/` prompt menu | **done** | keyboard-navigable listbox |
| AI agents CRUD | **done** | edit UI added (PATCH was unused) |
| **Agent Skills (SKILL.md)** | **done** | import .md/.zip (base64), attach to agents; instructions are inlined into the system prompt — **never executed** |
| Built-in prompt/agent catalogs | **skipped** | proprietary content; ship your own |

## Chat experience (new)

| Feature | Status | Notes |
|---|---|---|
| **Multi-model responses + Finalize** | **done** | fan-out bubbles + optional synthesis step; editable finalize instruction (Settings → Prompts) |
| **Memory** | **done** | global scope: auto-extracted after each exchange (editable internal prompt) + manual CRUD in a Memory panel; injected as system context; per-agent/project scoping deferred |
| **Side chat** | **done** | select text → "Ask…" (or "Search web") chip → floating panel; one-shot `/api/side-chat`, model picker, nothing persisted |
| **Temporary chats** | **done** | hidden from lists/search/export, unshareable, "Keep" banner; deleted when abandoned |
| **Full-screen composer** | **done** | ⌘⇧F or toolbar button |
| **Wake lock during generation** | **done** | Screen Wake Lock API, best-effort |
| Mermaid diagrams | **done** | lazy-vendored (2.5 MB loads on first diagram only), click-to-zoom, sanitized |
| LaTeX math (KaTeX) | **done** | `$…$`, `$$…$$`, `\[…\]`, \(…\); lazy-vendored with fonts |
| Table copy / expand | **done** | TSV copy + fullscreen view on every rendered table |
| CodePen export | **done** | button on js/css code blocks |

## Data

| Feature | Status | Notes |
|---|---|---|
| Export JSON / Markdown / HTML + import | **done** | HTML + per-chat export now surfaced in the UI (backend had them) |
| **Import from ChatGPT / Claude** | **done** | `conversations.json` (reply-graph linearised) and `chats.json` auto-detected in `/api/import` |
| Secret share links | **done** | capability URLs, revocable; **share management list** in Settings → Data; temporary chats excluded |
| Token usage + cost estimation | **done** | cached-token counts when reported; costs via user-set prices |
| **Usage dashboard** | **done** | 30-day daily chart (hand-rolled SVG, dual-theme) + per-model table from `/api/stats` |
| Documents / knowledge base | **done** | hybrid retrieval: FTS **+ vector embeddings** (any OpenAI-compatible /embeddings endpoint, SQLite-stored vectors, cosine top-k merged with FTS); degrades to FTS-only without config |
| Image inputs (vision) | **done** | |
| Text file attach | **done** | plus **PDF → text extraction** via pypdf (server-side, opt-in dep) |
| **Attachment/document previews** | **partial** | documents preview in-modal; PDF rendering via lazy pdf.js available; no per-chat "Files" panel yet |
| **Image generation** | **done** | OpenAI-compatible `/images/generations` + Gemini image models; saved to uploads, dropped into the chat |
| Voice input / TTS | **done** | browser voices, Edge neural, **ElevenLabs** (key stored encrypted); player bar with seek |
| Draft autosave, sound, wide mode, `?q=` prefill | **done** | |
| Artifacts | **partial** | sandboxed iframe preview + in-app editing of assistant replies; no interactive canvas co-editing (Phase-3 candidate) |
| PWA | **partial** | manifest + **opt-in service worker** (cache keyed to backend REV, network-first `/api`, cache-first static) — off by default to avoid stale-asset bugs |

## Platform / UI

| Feature | Status | Notes |
|---|---|---|
| Dark/light/system theme | **done** | full token scale; code-highlight theme swaps with the effective theme |
| **Design system** | **done** | type/spacing/radius/shadow/z-index scales, semantic colors, 150–220 ms motion with `prefers-reduced-motion` guard |
| **Icons** | **done** | inline SVG sprite (Lucide-style), no emoji, labelled |
| **In-app dialogs** | **done** | confirm/form/template/share dialogs with focus trap + restore; no native `confirm()`/`prompt()` |
| **Accessibility** | **partial** | aria-labels, live regions, listbox keyboard nav, focus-visible, 44 px targets, both-theme contrast; full screen-reader pass still outstanding |
| **Responsive** | **partial** | 480/760/1024/1440 tiers, safe-area composer, drawer; not manually verified on a phone |
| **Toast actions** | **done** | stacked toasts with actions, error persistence, offline banner |
| **Message UX** | **done** | timestamps, model badge, per-message delete, retry on error cards, scroll-to-bottom FAB, context meter, "Load earlier" cap at 200 |
| **Keyboard shortcuts** | **done** | **rebindable** (Settings → Shortcuts, conflict-safe), `?` overlay reflects bindings |
| **Frontend architecture** | **done** | `static/js/*` native ES modules (no build step); 1,444-line app.js retired |
| Keyboard shortcuts ⌘K/⌘B/⌘⌥N/⌘⌥E/⌘⌥C | **done** | defaults unchanged |
| Optional single password | **done** | scrypt + signed cookie; sign-in reopens the last chat |
| Self-host (systemd/nginx/deb) | **done** | unchanged; new deps: `pypdf` only |
| Encrypted keys at rest | **done** | Fernet; secrets (ElevenLabs) stored the same way, hints only |

## Skipped by design

Client-side plugin framework / custom JS extensions / browser MCP (arbitrary
code execution — a **server-side** MCP client remains a gated Phase-3 option
requiring its own threat model), teams (SSO/SCIM/roles — contradicts the
single-user assumption), profiles (multiple key sets), Office/pptx/xlsx parsing
(paste-the-text works; PDF is covered), Gemini video upload, cloud sync (the
server is the sync point), full n-ary version trees, i18n, ElevenLabs-by-default
(optional), built-in prompt/skill marketplaces (the SKILL.md format is
supported; the store is not).

## Verification

- `uv run pytest tests/ -q` — **86 passed** (was 53): variants, finalize,
  side chat, memories + extraction, skills + injection, stats, image
  generation, hybrid retrieval, PDF extraction, import converters, temporary
  chats, reasoning/caching/language dialect bodies, deprecated models,
  ElevenLabs guards, new settings.
- `uv run --with playwright python scripts/browser_check.py` — **62/62**
  (was 46): adds variant switcher, pin toggle, message delete with in-app
  confirm, finalize, side chat (creates no server chat), full-screen composer,
  XSS-neutralization probe, memory panel, usage dashboard, shortcuts UI, plus
  the asset list (DOMPurify, modules, sw.js).

## UNRESOLVED

1. **Human pass still worthwhile** — phone layout (F-14 tiers are CSS-only,
   unverified on a real device), real audio/speech, screen-reader walkthrough.
2. **Live smoke coverage unchanged** — google/groq/openrouter verified live;
   OpenAI/DeepSeek (paid), Anthropic, Mistral, xAI, Perplexity, HuggingFace
   still unverified live. New surfaces (reasoning effort, caching, image
   generation, embeddings) are unit-tested against published contracts only.
3. **Google/Anthropic streaming** unit-tested against canned payloads, not live.
4. **Usage is estimated** for most OpenAI-compatible providers; real costs need
   user-set model prices (unchanged by design).
5. **One historic single-test flake** reappeared once during this batch
   (`test_reasoning_effort_reaches_openai_body` failed once in a full run,
   passed in isolation and on all reruns). Watch it.
6. **Session cookie rotation** still deferred (single-user assumption).
7. **Service worker** is opt-in and only exercised as an asset fetch (200) in
   the browser pass — actual offline behavior needs a manual pass after
   enabling it in Settings.
