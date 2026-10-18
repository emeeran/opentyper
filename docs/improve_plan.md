# OpenTyper Improvement Plan

*Generated 2026-10-18. Scope: (a) feature parity with TypingMind Premium as of the
October 2026 changelog, (b) a ground-up UI/UX polish pass. Sources: full repo
inventory (`app/` 2,440 LOC, `static/` 1,719 LOC, `docs/`, 53 pytest + 46
browser checks), TypingMind's official feature list + changelog (Oct 2026 back
~12 months), and `docs/parity.md` / `docs/recon-premium.md` / `docs/assumptions.md`.
Every proposal stays inside the project's constraints (assumption A9): vanilla JS,
no build step, vendored libraries only, single-user self-hosted (A2), server-side
proxy (A6).*

Effort key: **S** ≤ half day · **M** 1–3 days · **L** 3–10 days · **XL** > 10 days.

---

## 1. Current state snapshot

| Area | Status |
|---|---|
| Backend | Feature-complete for MVP scope; no TODO stubs. Gaps: no message DELETE, no documents update, no active-shares list, dead settings (`default_provider_id` / `default_model` stored but consumed by nothing, `app/api.py:1281-1298`), no SSE heartbeat, regenerate replaces rather than branches. |
| Frontend | One 1,444-line `static/app.js` (no modules), 180-line `style.css` with 11 CSS tokens, emoji as the icon set, one responsive breakpoint (760px), one keyframe, zero transitions, one ARIA attribute in the whole app. |
| Known defects | See P0 list below — one feature is broken outright; one XSS surface. |
| Tests | Strong backend suite (53 tests, real-SSE mock provider) + 46-check headless browser pass. No a11y, no perf, no phone verification. |

### P0 defects (fix regardless of anything else)

| # | Defect | Where | Fix |
|---|---|---|---|
| P0-1 | **Add-custom-provider is broken**: client POSTs to `/api/providers/create`, which doesn't exist (backend route is `POST /api/providers`, `app/api.py:192`); the button 404s and no automated check covers it despite parity claiming "done" | `static/app.js:858` | Correct the URL; add a browser check for the full 3-step create flow |
| P0-2 | **Unsanitized markdown = XSS surface**: `marked.parse()` output inserted raw in the app and on the public share page, while model output and shared chats are untrusted | `static/app.js:170`, `app/main.py:78` | Vendor DOMPurify (no CDN), sanitize every render path incl. share page; add tests injecting `<script>`/`onerror=` payloads |
| P0-3 | **"Use documents" send override only read when the params popover is open** | `static/app.js:560` | Track the flag in `state`, not the DOM |
| P0-4 | **Light-mode code blocks use a dark highlight theme on a light background** (vendored CSS is github-dark only; `pre` is forced to `var(--bg)`) | `static/index.html:8`, `static/style.css:80-83` | Vendor a light highlight theme too; swap via `html[data-theme]` + `prefers-color-scheme` |
| P0-5 | **Streaming render path won't scale**: full markdown re-parse + highlight.js on every SSE delta, then a whole-chat re-fetch/re-render after every message | `static/app.js:495-501, 531` | See §5.8 (throttle + incremental render) |
| P0-6 | Backend/client drift: export supports `html` and `chat_ids` but the UI never exposes them; spec says only json/md | `app/api.py:1362-1405` | Wire the buttons (F-24); update `docs/spec.md` |

---

## 2. TypingMind Premium — latest feature landscape (Oct 2026)

Grouped by TypingMind's own categories, with OpenTyper's current standing.

| Group | TypingMind Premium features | OpenTyper today |
|---|---|---|
| Models & params | GPT-6.x/Claude 5.x/Gemini 4-era catalogs, prompt caching (Claude/OpenAI/Gemini), reasoning effort `none→max`, custom endpoints | ✅ 10 providers + custom; ❌ prompt-caching toggle, ❌ reasoning effort |
| Chat experience | Multi-model responses **with a finalize/synthesis step** (editable instruction, per-model stats, fork-from-response), **chat threads** (regenerate/edit preserve variants), **side chat** ("Ask…" on selected text), **temporary chats**, fullscreen composer editor, language output control | ⚠️ fan-out exists, no finalize; ❌ threads; ❌ side chat; ❌ temporary; ❌ fullscreen editor; ❌ output language |
| Rich rendering | Native **Mermaid diagrams**, **LaTeX math**, table copy/expand-fullscreen, artifacts as **Interactive Canvas + Web App Builder** with versions and side panel | ⚠️ artifacts = sandboxed iframe preview only; ❌ Mermaid/LaTeX/table tools |
| Tools | **Plugins** (HTTP/JS/**MCP**), **Plugin Presets** (named tool sets, one-click switch), **Agent Skills** (SKILL.md, store/GitHub/ZIP), **Toolbox** (in-browser file/code execution → docs, charts, media), image generation, web-search plugins | ❌ all (deliberate skip: arbitrary code execution) |
| Knowledge | Knowledge Base RAG, **Memory** (global / agent-scoped / project-scoped, per-user) | ⚠️ FTS-only documents; ❌ memory |
| Attachments | PDF/Excel/Word/pptx parsing + click-to-preview, video upload (Gemini), attachment files panel per chat | ⚠️ images + small text files; ❌ PDF/Office/video, ❌ per-chat files panel |
| Organization | **Projects** (folder + model/agent + instructions + docs), drag-and-drop folders, bulk select, pin, tags, archive | ⚠️ folders without drag-drop; ❌ bulk select; pin has **no UI toggle** |
| Migration/sync | Import from ChatGPT/Claude, cloud sync | ❌ import converters (sync N/A — server is the sync point) |
| Platform | PWA w/ offline, shortcut rebinding, profiles (avatar/keys/instructions ×N), ElevenLabs TTS, stay-awake during generation, deprecated-model badges, shared chats show multiple responses | ⚠️ PWA w/o service worker; ❌ rebinding, profiles, ElevenLabs, stay-awake, badges; ⚠️ share page renders all fan-out bubbles (check parity) |
| Teams | SSO/SCIM/roles/analytics/branding | Skip — contradicts assumption A2 |

---

## 3. Strategy

1. **Fix before adding.** P0-1…P0-6 first; a parity table claiming "done" that 404s is worse than a missing feature.
2. **UI/UX is the cheapest differentiator.** TypingMind's moat is polish; OpenTyper's current UI is prototype-grade (emoji icons, zero motion, native `prompt()` dialogs). Phase 1 closes most of the perceived gap without touching the backend.
3. **Revisit skips where the cost collapsed.** The 2025 skip list was right then; several items are now cheap (pin toggle, HTML export button, table copy, Mermaid via lazy vendored lib, Wake Lock) or newly central to TypingMind's value (Memory, threads-lite, multi-model finalize). Three skips stand firm on principle: plugin/JS/MCP arbitrary execution, teams, profiles (§8).
4. **Clean-room discipline.** Everything below is specified from public docs/changelog behavior, not from TypingMind code.
5. **Server-first advantage.** TypingMind keeps state in localStorage; OpenTyper has a real backend — use it for the features TypingMind does awkwardly (server-side memory injection, share revocation, FTS across devices, PDF/doc parsing in Python where libraries are rich).

---

## 4. Roadmap

### Phase 0 — Correctness & security (≈2–3 days)

| ID | Item | Effort | Notes |
|---|---|---|---|
| F-01 | Fix add-custom-provider URL + browser check | S | P0-1 |
| F-02 | Vendor DOMPurify; sanitize app + share page; XSS regression tests | S | P0-2 |
| F-03 | Fix use-documents override; fix light-mode code theme | S | P0-3, P0-4 |
| F-04 | Add missing trivial backend endpoints: `DELETE /api/chats/{id}/messages/{mid}`, `GET /api/shares` (active list), `PATCH /api/documents/{id}` (rename/re-index); de-orphan `default_provider_id`/`default_model` (use in New chat) or drop them | M | Removes dead settings; enables F-20/F-23 |

### Phase 1 — UI/UX overhaul (≈1.5–2 weeks) → full spec in §5

| ID | Item | Effort |
|---|---|---|
| F-05 | Design tokens: expand 11 tokens → full scale (color ramp + semantic success/warn/info, spacing, radii, type scale, shadows, z-index ladder) | S |
| F-06 | Replace every emoji icon with an inline SVG sprite (Lucide paths, hand-vendored — no CDN, no build) | M |
| F-07 | Motion pass: 150–250ms transitions on hover/panel/modal/message-enter; `prefers-reduced-motion` guard | S |
| F-08 | Replace all native `confirm()`/`prompt()` with in-app dialogs (deletes, folder naming, 3-step provider form, `{{var}}` fill-in, share-link modal with copy button) | M |
| F-09 | Accessibility sweep: `aria-label` on all icon buttons, `role="dialog"`+focus trap+restore for modals, `aria-live` for streaming & toasts, `:focus-visible` rings, arrow-key nav + `role="listbox"` in `/` menu and search results, skip link, 44px touch targets ≤760px | M |
| F-10 | States: skeleton/spinner on first paint & model test, empty state with starter prompts, inline field errors, toast actions (Retry / Open) replacing the 3.5s dead toast, offline/connection indicator | M |
| F-11 | Message UX: timestamps, model badge + avatar chip, per-message delete (needs F-04), copy on user bubbles, scroll-to-bottom FAB, search-result → scroll-to-hit + highlight, context/token meter near composer | M |
| F-12 | Surface buried power features: pin toggle button (parity gap), HTML export + per-chat export buttons (F-24), prompt/agent **edit** (PATCH exists, unused), archive/share/tags promoted out of the params popover into the chat header menu | M |
| F-13 | Streaming perf: plain-text render during stream (throttled ≤10 fps markdown+highlight on trailing edge), incremental final highlight, append-only DOM after send (no whole-chat refetch), optional render cap for very long chats | M |
| F-14 | Responsive tiers: 480 / 760 / 1024 / 1440; composer ergonomics on mobile; sidebar drawer + backdrop; manual phone pass (parity UNRESOLVED #1) | M |
| F-15 | Split `static/app.js` into native ES modules (`js/api.js`, `js/markdown.js`, `js/chat.js`, `js/sidebar.js`, `js/modals.js`, `js/tts.js`, `js/main.js`) — no build step required; unblocks everything else | M |

### Phase 2 — Premium parity (≈3–5 weeks, ordered by value/effort)

**Chat experience**

| ID | Item | Effort | How |
|---|---|---|---|
| F-16 | **Rich rendering**: vendored lazy-loaded Mermaid (render ` ```mermaid ` blocks, click-to-zoom) and KaTeX (`$…$`/`$$…$$`); table **Copy** (TSV) + **Expand** (fullscreen modal) buttons | M | Lazy `import()` keeps first paint fast; both fail soft to code blocks |
| F-17 | **Threads-lite (version trees, truncated-linear)**: regenerate/edit-and-rerun keeps the prior assistant message as a variant (`variant_of` + `is_active` columns); ‹ 1/2 › switcher on the message. Reverses the 2025 skip — TypingMind's most-visible behavior, moderate cost | M | `app/db.py` migration + `app/api.py:1040` rerun path + message renderer |
| F-18 | **Multi-model finalize**: after a fan-out, optional "Finalize" button sends the replies to a chosen model for synthesis into one answer; editable finalize instruction in Settings (Internal Prompts) | M | Reuses streaming pipeline; new internal-prompt setting |
| F-19 | **Memory** (global scope first): `memories` table + CRUD + Settings toggle; best-effort background extraction after each exchange (same pattern as auto-title, `app/api.py:974-995`); injected as a system prefix alongside knowledge (`app/api.py:846-853`); memory editor UI with per-item delete. Agent/project scoping later | M–L | Highest-value TypingMind 2026 feature; server-side = better than TM's localStorage version |
| F-20 | **Side chat**: select text → "Ask…" floating panel; scratch chat (ephemeral or saved-to-folder); model picker in panel | M | New endpoint or ephemeral chat w/o history pollution |
| F-21 | **Reasoning effort** per chat (`none/low/medium/high/xhigh`): OpenAI `reasoning_effort`, Anthropic thinking budget, Google `thinkingConfig`; hidden for non-reasoning models | M | `app/providers.py:79-101` params tables |
| F-22 | **Temporary chat** (not persisted) + **stay-awake** during generation (Wake Lock API) + **fullscreen composer** toggle | S | All frontend |
| F-23 | **Language output control**: per-chat output-language/style setting appended to system prompt | S | |

**Data & migration**

| ID | Item | Effort | How |
|---|---|---|---|
| F-24 | Export surface: HTML button + per-chat export (`chat_ids`) | S | Backend done (`app/api.py:1362-1405`) |
| F-25 | **Import from ChatGPT / Claude exports**: server-side converters in `POST /api/import` (pure JSON transforms → OpenTyper schema) | M | High user value, no new deps |
| F-26 | **Attachment previews**: click-to-preview for text/code/CSV/images in-app; PDF via vendored pdf.js (lazy). Office formats stay out (§8) | M | |
| F-27 | **Drag-and-drop folders + shift-select bulk move/archive/delete** (reverses a skip; it's UX, not scale) | M | HTML5 DnD + checkbox selection mode |

**Platform**

| ID | Item | Effort | How |
|---|---|---|---|
| F-28 | **Keyboard shortcut rebinding** (Settings → Shortcuts; stored in settings JSON; conflict detection; defaults per A20) | M | |
| F-29 | **Prompt caching toggle** where requestable (Anthropic `cache_control` breakpoints; document that OpenAI/Gemini caching is automatic now — TypingMind's Oct 2026 changelog confirms Claude caching is auto too, so surface cache-hit token stats where providers report them) | S–M | Reverses recon "SKIP until asked" — you asked |
| F-30 | **Deprecated-model badges**: mark catalog ids missing from a provider's live `/models` refresh | S | Uses existing `refresh-models` (`app/api.py:249`) |
| F-31 | **ElevenLabs TTS** as an optional third backend beside browser + Edge (`app/tts.py` pattern; key in Settings) | S | Only if desired; paid third party |
| F-32 | **Service worker** (reverses the stale-asset skip): cache name keyed to a deploy-time content hash; network-first for `/api`, cache-first for `/static`; kill-switch header. Strictly opt-in behind Settings | M | |

### Phase 3 — Differentiators / optional bets

| ID | Item | Effort | Notes |
|---|---|---|---|
| F-33 | **Artifacts v2 (canvas-lite→real)**: side-panel editor for HTML/Markdown/code artifacts with version list, Edit + AI-rewrite actions (longer/shorter/custom instruction) routed through the existing chat endpoint, share artifacts with images | L | TypingMind's Interactive Canvas/Web App Builder; biggest remaining UX gap after Phase 2 |
| F-34 | **Agent Skills (SKILL.md)**: import a folder/zip with `SKILL.md` + reference files; attach skills to agents; body inlined into system prompt on use (no code execution — instructions only). Compatible with the open standard TypingMind uses | M | Clean-room, no-execution variant |
| F-35 | **Vector RAG upgrade**: optional SQLite-VSS/sqlite-vec embeddings over documents to fix the known paraphrase-recall weakness of FTS-only retrieval (`docs/parity.md:56`); hybrid FTS+vector ranking | M–L | Backend-only; UI unchanged |
| F-36 | **Image generation**: OpenAI Images + Gemini image models as a composer action (attach generated image to chat) | M | Reverses DALL-E skip only if wanted |
| F-37 | **PDF text extraction** for attachments/documents (pypdf) + per-chat **Files panel** (About-this-chat → Files, incl. generated images) | M | |
| F-38 | **MCP client (server-side)**: OpenTyper talks MCP *from the backend* — tools run server-side with keys never touching the browser; admin allow-list. This is the one plugin-adjacent item that doesn't violate the no-arbitrary-client-JS principle | XL | Genuine differentiator vs TypingMind's browser-side MCP; decide consciously |
| F-39 | **CodePen button + search-keyword suggestions** on selection | S | Marginal, trivial |
| F-40 | **Token/cost dashboard**: usage-over-time chart per provider/model from data OpenTyper already stores (TypingMind gates this behind Teams) | M | dataviz pass required (see §5.9) |

---

## 5. UI/UX design specification

Design language: **calm utilitarian pro-tool** — quiet chrome, content-forward,
fast, keyboard-first. No glassmorphism, no decoration; polish = spacing, motion,
states, and typography, not color volume. Everything below respects A9 (no
build step): tokens are plain CSS custom properties, icons are an inlined SVG
sprite, fonts stay system-stack (zero web-font requests).

### 5.1 Tokens (extend `static/style.css:1-18`)

```css
:root {
  /* color — keep the existing accent, add the missing ramp */
  --bg: --surface: --surface-2: --raised:;        /* 4-step elevation */
  --text: --text-2: --text-3:;                    /* 3-step text (light mode: #0f172a / #475569 / #64748b — never lighter than slate-500 for readable text) */
  --border: --border-strong:;
  --accent: --accent-hover: --accent-subtle:;     /* existing #4f6ef7 family */
  --success: --warning: --danger: --info:;        /* semantic; danger exists, others don't */
  /* type scale (1.25 ratio) */
  --fs-0: 13px; --fs-1: 15px; --fs-2: 17px; --fs-3: 20px; --fs-4: 24px;
  --lh-body: 1.65;                                /* chat readability */
  --measure: 72ch;                                /* line length cap for prose */
  /* space scale (4px base) */
  --s-1: 4px; --s-2: 8px; --s-3: 12px; --s-4: 16px; --s-5: 24px; --s-6: 32px; --s-7: 48px;
  --radius-1: 6px; --radius-2: 10px; --radius-3: 14px;   /* replaces the single 10px */
  --shadow-1: --shadow-2: --shadow-3:;            /* hover / popover / modal */
  --dur-1: 150ms; --dur-2: 220ms; --ease: cubic-bezier(.2,.7,.3,1);
  --z-menu: 30; --z-pop: 40; --z-drawer: 50; --z-modal: 60; --z-toast: 70;  /* explicit ladder */
}
```

Dark theme mirrors via the existing `html[data-theme]` + `prefers-color-scheme`
mechanism (`style.css:1-18`) — no new machinery, just more variables.

### 5.2 Icons (F-06)

- One hidden `<svg><symbol id="i-…">` sprite in `index.html`; `<svg class="icon"><use href="#i-send">`.
- Lucide 24×24 paths, stroke-based, `currentColor`, fixed `viewBox` — sizes via one `.icon` class (16/18/20), never ad-hoc.
- Replace: 📎 🎙 ⚙ ✎ ⑂ ⭐ 🗑 ↻ … all of them. No emoji as UI icons anywhere (share page too, `app/main.py:59-81`).
- Every icon-only button gets `aria-label`.

### 5.3 Type & layout

- Body 15px desktop / **16px ≤760px**; chat measure capped `--measure` (65–75ch) even in wide mode — wide mode widens the canvas, not the paragraph.
- Sidebar: 260px desktop, 80px icon rail optional at ≥1440 (behind wide mode).
- Consistent 8px rhythm; popovers/modals share `--radius-2`, `--shadow-2`, and one padding scale.

### 5.4 Motion (F-07)

| Element | Motion |
|---|---|
| Buttons/menu items | `background/border-color` 150ms, no scale transforms (no layout shift) |
| Popover/dropdown | opacity + 4px translateY, 150–220ms, `transform` only |
| Modal | overlay fade 150ms + panel scale .97→1 220ms |
| Message enter | opacity + 6px rise 200ms on append only |
| Streaming cursor | 1px blinking caret block after last char |
| Sidebar drawer (mobile) | translateX 220ms + backdrop fade |
| All | wrapped in `@media (prefers-reduced-motion: reduce) { * { transition: none; animation: none } }` — currently absent |

### 5.5 Components to (re)build

| Component | Change |
|---|---|
| **Dialogs** (F-08) | One `role="dialog"` + `aria-modal` + labelled heading; focus trap on open, restore to invoker on close; Escape closes; used for delete-confirm, rename, provider-create (single form, not 3 `prompt()`s), `{{var}}` fill-in, share-link (readonly input + Copy + QR optional). Delete dialog names the object and states consequence. |
| **Toasts** (F-10) | `role="status"` `aria-live="polite"`, stack bottom-right, 6s + hover-pause, action button (Retry / View / Undo), error toasts persist until dismissed. |
| **Composer** | Attach/mic/tools cluster with tooltips; token+context meter (uses stored usage + context_limit); model chip showing provider icon; fullscreen toggle (F-22); drag-file-onto-composer to attach; paste-image already works — add paste-CSV/text-as-file for >2k chars (TypingMind behavior). |
| **Message block** | Header row: role avatar chip, model badge, timestamp; hover-revealed action bar (copy / speak / edit / rerun / branch (F-17) / delete); variant switcher ‹ 2/3 ›; persisted error rows get a distinct bordered card with a Retry action, not red markdown. |
| **Sidebar** | Section labels; folder rows expandable with drag-drop targets (F-27); pin star is a real toggle (F-12); per-row kebab menu (rename/move/tags/archive/share/delete) replacing hover-icon clutter; selection mode for bulk actions. |
| **Chat header** | Title (inline edit), model picker (searchable combobox — the native `<select>` struggles with 100+ model ids), kebab with promoted actions (F-12), context meter. |
| **Settings** | 4 tabs → 6: General, Providers, **Shortcuts** (F-28), **Prompts (internal)** (F-18), Data, Appearance. Providers tab gets the fixed create form, per-provider test results inline, key hint + masked state. |
| **Empty/loading states** (F-10) | New-chat view: agent quick-picks + 3 starter prompts; first paint: skeleton message shapes; provider test: inline spinner + latency chip. |
| **Search** | Results as `role="listbox"` with ↑/↓/Enter, hit-context snippets (already returned by FTS5), click scrolls to and flashes the message (F-11). |

### 5.6 Accessibility (F-09) — acceptance checklist

- [ ] All interactive elements reachable in a sensible Tab order; visible `:focus-visible` ring (2px `--accent`, 2px offset) — none exist today.
- [ ] Every icon-only button labelled; modal focus trapped + restored; dialogs `aria-modal`.
- [ ] Streaming text and toasts announced via `aria-live` (polite) without stealing focus.
- [ ] `/` prompt menu, model combobox, search results: full arrow-key + Enter + Escape, `role="listbox"/"option"`, `aria-activedescendant`.
- [ ] Contrast ≥ 4.5:1 for text in **both** themes (light-mode muted text currently reaches for gray-400 territory — floor it at slate-500/`--text-3`).
- [ ] Touch targets ≥ 44×44px ≤760px; `cursor: pointer` on all clickables.
- [ ] `prefers-reduced-motion` honored; `prefers-color-scheme` default respected.
- [ ] Run axe-core (vendored, dev-only script) + a manual VoiceOver/NVDA pass as part of the browser-check expansion.

### 5.7 Responsive (F-14)

| Breakpoint | Layout |
|---|---|
| <480 | Single column; sidebar full-screen drawer; composer pinned w/ safe-area insets; actions collapse into kebab; 16px body |
| 480–760 | As today's drawer, plus composer ergonomics (larger send target, sticky toolbar) |
| 760–1024 | Persistent sidebar; params popover → side sheet |
| ≥1024 | As today + optional wide mode; ≥1440 optional icon rail |

### 5.8 Rendering performance (F-13 / P0-5)

1. During streaming: append text nodes; re-parse markdown + highlight at most every 150ms (trailing-edge throttle) instead of per-delta.
2. Final chunk: one full parse + highlight + DOMPurify pass.
3. After `done`: append the finalized bubble; stop re-fetching/re-rendering the whole chat (`app.js:531`) — the server stream already carries `meta` (message_id, usage).
4. Auto-scroll: stick-to-bottom only when already at bottom; FAB otherwise.
5. Long chats: paginate message rendering (render last N, "Load earlier" button) — no virtualization lib needed at single-user scale.

### 5.9 Charts (F-40)

If the usage dashboard is built, follow the dataviz skill before writing chart code: single system, accessible categorical palette validated in both themes, table alternative provided. Candidate: vendored uPlot (small, canvas, vanilla-friendly).

---

## 6. Engineering enablers

1. **Module split (F-15)** — native ES modules, no bundler: `<script type="module" src="js/main.js">`. Vendored libs stay classic scripts defining globals (`marked`, `hljs`, `DOMPurify`, lazy `mermaid`/`katex`/`pdfjs` via dynamic `import()` of vendored files). Order matters only at boot; `main.js` keeps today's boot sequence (`app.js:1428-1444`).
2. **Vendoring discipline** — new deps: `dompurify.min.js` (~10KB), `mermaid.min.js` (~1.4MB, lazy), `katex` (~300KB + fonts, lazy), `pdf.js` (~350KB, lazy), light+dark highlight themes. Record each in `docs/spec.md` with size + license; keep the no-CDN rule absolute.
3. **DB migrations** — `variant_of`/`is_active` (F-17) and `memories` (F-19) follow the existing idempotent `ALTER TABLE` pattern (`app/db.py:153-167`); add column-remigration tests like the existing one.
4. **API contract** — update `docs/spec.md` + `docs/parity.md` with every Phase-2 endpoint; parity table is the project's source of truth and P0-1 shows what drift costs.
5. **Version stamp** — inject a content hash into the service worker cache name and a `/api/health` build field to make F-32's stale-asset risk genuinely manageable.

---

## 7. Test & verification plan

| Layer | Additions |
|---|---|
| pytest (now 53) | XSS sanitize unit tests; message DELETE + shares list; variant threads (regenerate keeps inactive variant, switcher payload); finalize endpoint; memories CRUD + injection + no-leak into share pages; reasoning-effort per dialect body assertions (extend `test_text_only_body_adapters`); ChatGPT/Claude import converters (fixture exports); `PATCH /api/documents`; re-migration tests for new columns |
| browser_check.py (now 46) | custom-provider create E2E (P0-1 regression); share page `<script>` payload neutralized; pin toggle; message delete; variant switcher; finalize flow; Mermaid/KaTeX render smoke (lazy chunk loads); side chat; temporary chat leaves no server row; fullscreen composer; shortcut rebinding takes effect; keyboard-only walkthrough of `/` menu + search; axe-core a11y scan with zero critical violations |
| Manual gates | Real-phone pass at 375px (parity UNRESOLVED #1); both-theme contrast spot check; `prefers-reduced-motion` walkthrough; live smoke extended to ≥1 more provider when a key is available (UNRESOLVED #2) |
| Perf budget | 60fps scroll in a 500-message chat; <16ms p95 between stream frames; first contentful paint unchanged despite vendored libs (lazy-load guard: assert no >100KB script loads before first interaction in the browser check) |

---

## 8. Still out of scope (skip list, reaffirmed)

| Item | Reason (unchanged from `docs/recon-premium.md`) |
|---|---|
| Client-side plugin framework / custom JS extensions / browser MCP | Arbitrary code execution; no sandbox worth shipping. F-38 is the server-side exception and still needs its own threat-model pass |
| Teams (SSO/SCIM/roles/analytics admin) | Contradicts single-user assumption A2 |
| Profiles (multiple key sets/avatars) | Providers are swappable per chat; near-zero value for one user |
| Office/pptx/xlsx attachment parsing | Heavy vendored parsers for a paste-the-text workflow; revisit with F-37 if demand appears |
| Gemini video upload | Large-binary pipeline for one provider |
| Cloud sync | Server already is the sync point |
| Full version trees (n-ary, rebase, merge) | F-17 delivers the 90% (variants on regenerate/edit) at 10% of the complexity |
| i18n | Single-user, English UI |
| ElevenLabs-by-default | Optional (F-31), never default — paid third party |
| Built-in prompt/agent marketplaces | Proprietary content; ship-your-own stands (F-34 adds the *format*, not the store) |

---

## 9. Suggested execution order

```
Week 1        Phase 0 (F-01..F-04)  →  release as a bugfix cut
Weeks 2-3     F-15 module split → F-05..F-07 tokens/icons/motion → F-08..F-11
              (each merges green behind the existing 46 browser checks)
Week 4        F-12..F-14; first phone pass; a11y checklist green
Weeks 5-6     F-16 rich rendering · F-17 threads-lite · F-22/F-23 quick wins
Weeks 7-8     F-19 memory · F-18 finalize · F-21 reasoning effort
Weeks 9-10    F-25 import converters · F-24/F-30 · F-27 drag-drop/bulk
Week 11+      F-28..F-32 platform batch, then Phase-3 bets by preference:
              F-33 artifacts v2 > F-35 vector RAG > F-34 skills > F-40 dashboard
```

**Quick wins if you only have one day:** F-01 (fix broken provider form) · F-02 (sanitize) · pin-toggle button · HTML export button · table copy · fullscreen composer · Wake Lock · message timestamps.

## 10. Acceptance criteria

1. Every parity table row moved from partial/missing → done carries at least one automated check (pytest or browser_check) — no more "done" that 404s.
2. axe-core critical violations: 0. Keyboard-only user can: new chat, insert prompt with variables, switch model, search, open settings, share — unaided.
3. 375px phone pass: no horizontal scroll, all actions reachable, composer usable one-handed.
4. Both themes: contrast ≥ 4.5:1 for text, code blocks legible in light mode.
5. 500-message chat scrolls at 60fps; streaming renders without whole-chat re-renders.
6. `docs/parity.md` and `docs/spec.md` updated in the same merge as any behavior change.
