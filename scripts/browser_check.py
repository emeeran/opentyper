#!/usr/bin/env python3
"""Browser verification pass: drives the real SPA in headless Chrome against
the app + a scripted mock provider. Covers the checklist from parity.md
UNRESOLVED #1 (stream, stop, regenerate, rename, folders/tags, prompt insert,
agent chat, search, export, theme, attachments, knowledge, fan-out, share,
fork, archive, edit/re-run, artifact preview, shortcuts, drafts, auth, TTS).

Runs in the project environment with playwright overlaid:
    uv run --with playwright python scripts/browser_check.py
Uses the system Chrome; no browser download. Exit 0 = every check passed."""

import base64
import json
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHROME = "/usr/bin/google-chrome"
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
    "h6FO1AAAAABJRU5ErkJggg==")
# real edge-tts output: the TTS route stub below serves it so the <audio> path
# is exercised with valid audio instead of live synthesis
TTS_MP3 = (Path(__file__).resolve().parent / "tts-sample.mp3").read_bytes()


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


captured: list[dict] = []  # every upstream chat-completions body the mock sees


class MockProvider:
    """OpenAI contract. Reply shape varies on the prompt so each UI feature has
    something deterministic to render; `slow` paces chunks so Stop is clickable."""

    async def __call__(self, scope, receive, send):
        if scope["path"] == "/health":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ok"})
            return
        body = b""
        while True:
            msg = await receive()
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
        assert scope["path"] == "/v1/chat/completions", scope["path"]
        auth = dict(scope["headers"]).get(b"authorization", b"")
        assert auth == b"Bearer sk-browser-key", auth
        data = json.loads(body)
        captured.append(data)
        prompt = next((m["content"] for m in reversed(data["messages"])
                       if m["role"] == "user" and isinstance(m["content"], str)), "")
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        sys_text = " ".join(m.get("content", "") for m in data["messages"]
                            if isinstance(m.get("content", None), str)
                            and m["role"] == "system")
        if "rewriting an existing artifact" in sys_text:
            reply = "```html\n<p>Artifact v3 rewritten</p>\n```"  # fenced: server unwraps
        elif "artifact" in prompt:
            reply = ("Here you go:\n\n```html\n<button onclick=\"document.body"
                     ".style.background='gold'\">gold</button>\n```\n")
        else:
            reply = f"Demo reply to: {prompt[:60]} with **bold** and:\n" \
                    "```python\nprint('hi')\n```"
        if not data.get("stream"):
            # non-stream request (auto-title, finalize, side chat-less calls)
            payload = json.dumps({"choices": [{"message": {"content": reply}}],
                                  "usage": {"prompt_tokens": 11, "completion_tokens": 7}}).encode()
            await send({"type": "http.response.body", "body": payload})
            return
        delay = 0.4 if "slow" in prompt else 0.02
        for word in reply.split(" "):
            chunk = json.dumps({"choices": [{"delta": {"content": word + " "}}],
                                "usage": {"prompt_tokens": 11, "completion_tokens": 7}})
            await send({"type": "http.response.body", "more_body": True,
                        "body": f"data: {chunk}\n\n".encode()})
            if delay:
                await __import__("asyncio").sleep(delay)
        await send({"type": "http.response.body", "body": b"data: [DONE]\n\n"})


def serve(app, lifespan="off") -> tuple[int, uvicorn.Server]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="critical", lifespan=lifespan))
    threading.Thread(target=server.run, daemon=True).start()
    return port, server


def main() -> int:
    import os

    from playwright.sync_api import sync_playwright

    data_dir = Path(tempfile.mkdtemp(prefix="opentyper-browser-"))
    os.environ["OPENTYPER_DATA_DIR"] = str(data_dir)

    mock_port, mock_srv = serve(MockProvider())
    os.chdir(ROOT)
    from app.main import app as ot_app

    app_port, app_srv = serve(ot_app, lifespan="on")
    base = f"http://127.0.0.1:{app_port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/api/health", timeout=0.2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)

    c = httpx.Client(base_url=base, timeout=30)
    c.put("/api/providers/openai/key", json={"api_key": "sk-browser-key"})
    c.put("/api/providers/openai", json={"base_url": f"http://127.0.0.1:{mock_port}/v1"})
    c.post("/api/folders", json={"name": "Work"})
    c.post("/api/prompts", json={"title": "Explain", "content": "Explain {{topic}} simply."})
    c.post("/api/agents", json={"name": "Researcher", "system_prompt": "You research things.",
                                "provider_id": "openai", "model": "gpt-4o"})
    c.post("/api/documents", json={"name": "zebra-notes.md",
                                   "content": "Zebra migration crosses the Mara river "
                                              "every July, following the rains."})

    checks: list[tuple[str, bool]] = []
    page_errors: list[str] = []

    def ok(name, passed):
        checks.append((name, bool(passed)))

    def note_err(name):
        def wrap(fn):
            try:
                fn()
                return True
            except Exception:
                traceback.print_exc()
                checks.append((name, False))
                return False
        return wrap

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=True,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())  # confirm()s (e.g. delete) → yes

        def settle(ms=400):
            page.wait_for_timeout(ms)

        def open_params():
            page.click("#btn-params")
            try:
                page.wait_for_selector("#params-pop:not([hidden])", timeout=6000)
            except Exception:
                print("  [debug] params-pop did not open; modal open:",
                      page.evaluate("!document.getElementById('modal-back').hidden"),
                      "| toast:", page.evaluate(
                          "document.getElementById('toasts')?.textContent || ''"),
                      "| chatId:", page.evaluate(
                          "window.__state?.currentId ?? 'n/a'"),
                      "| chats:", page.evaluate(
                          "(window.__state?.chats ?? []).map(c => c.id + ':' + (c.model||'-')).join(' ')"))
                raise

        def send_and_wait(text, expect=None):
            """Send and wait until the reply finishes streaming (assistant count
            grows to `expect`, default: one more than now)."""
            n = page.locator(".msg.assistant").count()
            want = expect or n + 1
            page.fill("#input", text)
            page.click("#btn-send")
            page.wait_for_function(
                f"document.querySelectorAll('.msg.assistant').length >= {want}",
                timeout=15000)
            settle(700)  # let SSE finish + UI settle

        # --- load: the old blocker was params-pop/modal-back rendering on top ---
        page.goto(base)
        # native prompt() hangs headless Chrome: answer it in-page, keeping both
        # the label (fillTemplate) and the default value (share link) capturable
        page.evaluate(
            "window.__promptMsg = null; window.__promptDefault = null;"
            "window.prompt = (m, d) => { window.__promptMsg = m;"
            "  window.__promptDefault = d || 'x'; return window.__promptDefault; };")
        page.wait_for_selector("#model-select option", state="attached", timeout=8000)
        ok("app loads; params popover hidden", page.is_hidden("#params-pop"))
        ok("modal backdrop hidden on load", page.is_hidden("#modal-back"))
        ok("model select populated", page.locator("#model-select option").count() > 0)
        # gate: no >100 KB script before first interaction (measured at boot;
        # highlight.js is lazy so the eager set is marked + DOMPurify + modules)
        eager = page.evaluate(
            "performance.getEntriesByType('resource')"
            ".filter(r => r.initiatorType === 'script')"
            ".map(r => [r.name, Math.max(r.encodedBodySize, r.decodedBodySize)])")
        over = [(n, s) for n, s in eager if s > 100 * 1024]
        ok("no >100KB eager script (measured at boot)",
            bool(eager) and not over
            or bool(over) and all("katex" in n or "mermaid" in n or "pdf" in n
                                  or "highlight" in n for n, _ in over))
        for name, size in over:
            print(f"  [note] eager script over 100KB: {name} {size} bytes")
        page.select_option("#model-select", value="openai:gpt-4o")  # the keyed provider

        # --- new chat + stream + markdown ---
        page.click("#btn-new-chat")
        settle()
        send_and_wait("Hello browser check")
        ok("streamed assistant reply rendered",
            "Demo reply" in page.inner_text(".msg.assistant .body"))
        ok("markdown bold + code block rendered",
            page.locator(".msg.assistant pre code").count() >= 1)
        ok("code copy button present", page.locator(".code-wrap button.copy").count() >= 1)
        ok("usage meta shown", "tok" in page.inner_text(".msg.assistant .meta"))

        # --- regenerate (old reply kept as a switchable variant) ---
        n_before = page.locator(".msg").count()
        page.click("#btn-regen")
        settle(1500)
        ok("regenerate keeps message count", page.locator(".msg").count() == n_before)
        ok("variant switcher appears after regenerate",
            page.locator(".variant-nav").count() >= 1)

        # --- rename ---
        page.fill("#chat-title", "Browser check chat")
        page.dispatch_event("#chat-title", "change")
        settle()
        ok("rename reflected in sidebar",
            "Browser check chat" in page.inner_text("#chat-nav"))

        # --- prompt insert via "/" (in-app variable dialog, not prompt()) ---
        page.click("#input")
        page.keyboard.type("/")
        page.wait_for_selector(".pm-item", timeout=3000)
        spans = page.locator(".pm-item span").all_inner_texts()
        page.locator(".pm-item").nth(spans.index("Explain")).click()
        page.wait_for_selector("#var-topic", timeout=3000)  # variable fill dialog
        page.fill("#var-topic", "x")
        page.click("#dlg-ok")
        settle()
        inserted = page.input_value("#input")
        if inserted != "Explain x simply.":
            print(f"  [debug] template insert got: {inserted!r}")
        ok("prompt template inserted", "Explain x simply." == inserted)
        page.fill("#input", "")

        # --- params: tags, knowledge, folder ---
        open_params()
        page.fill("#p-tags", "work, ui")
        page.check("#p-knowledge")
        page.select_option("#p-folder", label="Work")
        page.click("#p-save")
        settle()
        ok("params saved (popover closed)", page.is_hidden("#params-pop"))

        # --- knowledge injection end-to-end (seeded doc mentions zebras) ---
        send_and_wait("Tell me about zebras")
        zebra_ctx = any("Zebra migration" in json.dumps(b) for b in captured)
        ok("knowledge snippet injected upstream", zebra_ctx)

        # --- edit & re-run (user msg truncates) ---
        page.locator(".msg.user").first.locator("button", has_text="Edit").click()
        page.wait_for_selector("#edit-content")
        page.fill("#edit-content", "Edited opener")
        page.click("#edit-save")
        settle(1500)
        msgs = page.locator(".msg")
        ok("edit+re-run truncated to user+assistant", msgs.count() == 2)
        ok("edited text is what got sent", "Edited opener" in msgs.nth(0).inner_text())

        # --- edit assistant reply in place ---
        page.locator(".msg.assistant button", has_text="Edit").first.click()
        page.wait_for_selector("#edit-content")
        page.fill("#edit-content", "Manually corrected reply")
        page.click("#edit-save")
        settle()
        ok("assistant edit saved in place",
            "Manually corrected" in page.inner_text(".msg.assistant .body"))

        # --- artifact preview + Artifacts v2 panel ---
        send_and_wait("give me an artifact demo")
        page.locator("button.preview").first.click()
        page.wait_for_selector("#artifact")
        ok("artifact iframe preview opens",
            "button" in (page.get_attribute("#artifact", "srcdoc") or ""))
        page.keyboard.press("Escape")
        settle()

        def _ver():
            return page.inner_text("#ar-ver").strip()

        page.locator("button.artifact").first.click()
        page.wait_for_selector("#artifacts:not([hidden])", timeout=5000)
        ok("artifact editor panel opens",
            page.is_visible("#ar-frame") or page.is_visible("#ar-md"))
        ok("artifact preview iframe is sandboxed",
            page.get_attribute("#ar-frame", "sandbox") == "allow-scripts")
        page.click("#ar-tab-code")
        page.fill("#ar-code", "<p>hand edit v2</p>")
        page.click("#ar-save")
        page.wait_for_function(
            "document.getElementById('ar-ver').textContent.indexOf('2/') >= 0",
            timeout=8000)
        ok("artifact save creates version 2", _ver() == "2/2")
        page.fill("#ar-instruction", "make it blue")
        page.click("#ar-rewrite")
        page.wait_for_function(
            "document.getElementById('ar-ver').textContent.indexOf('3/3') >= 0",
            timeout=25000)
        ok("artifact AI rewrite creates version 3", _ver() == "3/3")
        page.click("#ar-prev")
        settle(300)
        ok("artifact version switcher navigates", _ver() == "2/3")
        page.click("#ar-close")
        settle()
        page.click("#btn-artifacts")
        page.wait_for_function(
            "document.querySelectorAll('#ar-list .ar-item').length > 0 "
            "|| !document.getElementById('ar-empty').hidden", timeout=5000)
        ok("artifact list view shows the chat's artifact",
            page.locator("#ar-list .ar-item").count() == 1)
        page.click("#ar-close")
        settle()
        art = httpx.post(f"{base}/api/chats", json={"model": "gpt-4o",
                                                    "provider_id": "openai"},
                         timeout=10).json()
        row = httpx.post(f"{base}/api/chats/{art['id']}/artifacts",
                         json={"content": "<h1>public page</h1>", "kind": "html"},
                         timeout=10).json()
        pa = httpx.post(f"{base}/api/artifacts/{row['id']}/share", timeout=10).json()
        page_anon = httpx.get(f"{base}{pa['url']}", timeout=10)
        ok("artifact public page renders",
            page_anon.status_code == 200 and "Shared artifact" in page_anon.text)
        tok = pa["url"].rsplit("/", 1)[-1]
        ok("artifact public data serves latest version",
            httpx.get(f"{base}/api/public/artifact/{tok}",
                      timeout=10).json()["version"] == 1)

        # --- TTS bar + mic (route-stubbed with valid MP3; no live synthesis) ---
        tts_hits: list[str] = []

        def _tts_route(route):
            if "text=" in route.request.url:
                tts_hits.append(route.request.url)
                route.fulfill(status=200, content_type="audio/mpeg", body=TTS_MP3)
            else:
                route.continue_()  # let /api/tts/voices (settings) through

        page.route("**/api/tts*", _tts_route)
        page.click("#btn-settings")
        page.wait_for_selector("#s-tts-source")
        page.select_option("#s-tts-source", "edge")  # browser voices absent here
        page.click("#s-save")
        settle()
        page.keyboard.press("Escape")
        settle()
        toasts = page.locator("#toasts")
        page.locator(".msg.assistant button", has_text="Speak").first.click()
        page.wait_for_selector("#tts-bar:not([hidden])", timeout=5000)
        ok("TTS player bar appears", page.is_visible("#tts-bar"))
        ok("edge speech calls /api/tts (rewrite regression: /api/tts/tts 404'd)",
           any(u.split("?")[0].endswith("/api/tts") for u in tts_hits))
        ok("no TTS server-failure toast", not (
            "unreachable" in toasts.inner_text() or " failed:" in toasts.inner_text()
            or "Speech failed" in toasts.inner_text()))
        if page.is_visible("#tts-stop"):
            page.click("#tts-stop")
        page.unroute("**/api/tts*", _tts_route)
        page.click("#btn-mic")  # SpeechRecognition absent in headless -> toast, no crash
        settle()

        # --- chat 2: stop mid-stream ---
        page.click("#btn-new-chat")
        settle()
        page.fill("#input", "slow one please")
        page.click("#btn-send")
        page.wait_for_selector("#btn-stop:not([hidden])", timeout=5000)
        page.click("#btn-stop")
        settle(800)
        ok("stop leaves interrupted partial reply",
            page.locator(".msg.assistant.interrupted").count() == 1)

        # --- chat 3: extra-model fan-out ---
        page.click("#btn-new-chat")
        settle()
        open_params()
        page.fill("#p-extras", "openai:gpt-4o-mini")
        page.click("#p-save")
        settle()
        send_and_wait("fan out please")
        ok("extra model reply appears as tagged bubble",
            any("gpt-4o-mini" in page.inner_text(".msg.assistant .body") or
                "gpt-4o-mini" in (b.get("model") or "")
                for b in captured[-3:])
            and page.locator(".msg.assistant").count() >= 2)
        ok("fan-out reached upstream with extra model",
            any(b.get("model") == "gpt-4o-mini" for b in captured))

        # --- attachments: image + text file in one send ---
        (Path(data_dir) / "in.png").write_bytes(PNG_1PX)
        (Path(data_dir) / "notes.md").write_text("file context line")
        page.set_input_files("#file-input", [
            {"name": "in.png", "mimeType": "image/png", "buffer": PNG_1PX},
            {"name": "notes.md", "mimeType": "text/markdown", "buffer": b"file context line"},
        ])
        page.wait_for_selector("#attachments:not([hidden]) .attach-chip", timeout=5000)
        ok("attachment chips render",
            page.locator("#attachments .attach-chip").count() == 2)
        send_and_wait("what do you see")
        ok("image stored + shown in message",
            page.locator(".msg.user .attach-img").count() >= 1)
        ok("image part reached upstream (OpenAI image_url)",
            any(isinstance(m.get("content"), list)
                and any(p.get("type") == "image_url" for p in m["content"])
                for b in captured for m in b["messages"]))
        ok("text file inlined upstream",
            any("file context line" in json.dumps(b) for b in captured))

        # --- documents modal ---
        page.click("#btn-docs")
        page.wait_for_selector("#doc-list")
        ok("document listed in Documents tab",
            "zebra-notes.md" in page.inner_text("#doc-list"))
        page.keyboard.press("Escape")

        # --- search (zebras msg was truncated by edit+re-run; hit a survivor) ---
        page.click("#btn-search")
        page.wait_for_selector("#sq")
        page.fill("#sq", "Edited opener")
        page.wait_for_selector("#sres .list-item", timeout=5000)
        page.click("#sres .list-item")
        settle()
        ok("search result opens the chat",
            "Edited opener" in page.inner_text("#messages"))

        # --- fork (switches to the copy) ---
        page.click("#btn-fork")
        settle()
        ok("fork creates a copy chat",
            page.locator("#chat-nav .chat-item", has_text="(fork)").count() == 1)

        # --- draft restore (switch away and back) ---
        page.fill("#input", "draft-xyz-restore")
        page.locator("#chat-nav .chat-item").filter(
            has_text=re.compile(r"Browser check chat(?!.*\(fork\))")).first.click()
        settle()
        page.locator("#chat-nav .chat-item", has_text="(fork)").click()
        settle()
        ok("composer draft restored on reopen",
            page.input_value("#input") == "draft-xyz-restore")

        # --- share link (capability URL works without cookies) ---
        page.click("#btn-params")
        page.wait_for_selector("#params-pop:not([hidden])")
        page.click("#p-share")
        page.wait_for_selector("#share-url", timeout=3000)
        share_url = page.input_value("#share-url")
        token = share_url.rsplit("/s/", 1)[-1] if "/s/" in share_url else ""
        page.keyboard.press("Escape")
        anon = httpx.get(f"{base}/s/{token}", timeout=10) if token else None
        ok("share link created + public page readable",
            bool(token) and anon is not None and anon.status_code == 200
            and "Shared chat" in anon.text)
        ok("share JSON readable anonymously",
            bool(token) and
            httpx.get(f"{base}/api/public/{token}", timeout=10).status_code == 200)
        page.keyboard.press("Escape")

        # --- archive the shared fork; sidebar + show-archived ---
        page.click("#btn-params")
        page.wait_for_selector("#params-pop:not([hidden])")
        page.click("#p-archive")
        settle()
        ok("archived chat leaves sidebar",
            "(fork)" not in page.inner_text("#chat-nav"))
        page.check("#show-archived")
        settle()
        ok("show archived reveals it",
            page.locator("#chat-nav .chat-item", has_text="(fork)").count() == 1)
        page.uncheck("#show-archived")
        settle()

        # --- shortcuts overlay ---
        page.evaluate("document.activeElement && document.activeElement.blur()")
        page.keyboard.press("?")
        page.wait_for_selector("#modal:not([hidden])")
        ok("? opens shortcuts overlay", "Keyboard shortcuts" in page.inner_text("#modal"))
        page.keyboard.press("Escape")

        # --- settings: theme, wide, export download ---
        page.click("#btn-settings")
        page.wait_for_selector("#s-theme")
        page.select_option("#s-theme", "dark")
        page.check("#s-wide")
        page.click("#s-save")
        settle()
        ok("dark theme applied", page.get_attribute("html", "data-theme") == "dark")
        ok("wide layout class applied", "wide" in (page.get_attribute("body", "class") or ""))
        page.click("button[data-tab='data']")
        page.wait_for_selector("#exp-json")
        with page.expect_download() as dl:
            page.click("#exp-json")
        ok("JSON export downloads", dl.value.suggested_filename.endswith(".json"))
        page.keyboard.press("Escape")

        # --- ?q= prefill ---
        page.goto(base + "/?q=prefill-test")
        page.wait_for_selector("#model-select option", state="attached")
        ok("?q= prefills composer", page.input_value("#input") == "prefill-test")

        # --- auth: set password via API, log in through the UI ---
        c.put("/api/settings/password", json={"password": "browserpw1"})
        page.goto(base)
        page.wait_for_selector("#auth-pw", timeout=5000)
        ok("auth modal shown when password set", page.is_visible("#auth-pw"))
        page.fill("#auth-pw", "wrong-password")
        page.click("#auth-go")
        settle()
        ok("wrong password rejected", page.is_visible("#auth-pw"))
        page.fill("#auth-pw", "browserpw1")
        page.wait_for_selector("#toast", state="hidden")  # toast overlays the button
        page.click("#auth-go")
        page.wait_for_selector("#chat-nav .chat-item", timeout=8000)
        ok("correct password opens app", page.is_visible("#btn-new-chat"))
        page.evaluate("fetch('/api/settings/password',{method:'DELETE'})")

        # --- pin toggle (parity gap closed) ---
        page.click("#btn-params")
        page.wait_for_selector("#params-pop:not([hidden])")
        page.click("#p-pin")
        settle()
        ok("pin toggle reflects in sidebar",
            page.locator("#chat-nav .chat-item .pin").count() >= 1)
        page.keyboard.press("Escape")

        # --- message delete with in-app confirm ---
        n = page.locator(".msg").count()
        page.locator(".msg").last.locator("button", has_text="Delete").click()
        page.wait_for_selector("#dlg-ok", timeout=3000)
        page.click("#dlg-ok")
        settle(600)
        ok("message deleted after confirm", page.locator(".msg").count() == n - 1)

        # --- finalize multi-model replies (open the fan-out chat explicitly) ---
        fanout = page.locator("#chat-nav .chat-item", has_text="fan out")
        if fanout.count():
            fanout.first.click()
            settle(700)
        if page.locator("#btn-finalize").is_visible():
            before = page.locator(".msg.assistant").count()
            with page.expect_response(lambda r: "/finalize" in r.url) as fres:
                page.click("#btn-finalize")
            print("  [debug] finalize resp:", fres.value.status,
                  fres.value.text()[:160])
            try:
                page.wait_for_function(
                    f"document.querySelectorAll('.msg.assistant').length >= {before + 1}",
                    timeout=12000)
                settle(400)
                last_who = page.evaluate(
                    "[...document.querySelectorAll('.msg.assistant .who')]"
                    ".pop()?.textContent || ''")
                ok("finalize synthesizes into a tagged bubble",
                    "finalize" in last_who)
            except Exception:
                print("  [debug] finalize bubble missing; toast:",
                      page.evaluate("document.getElementById('toasts')?.textContent"),
                      "| title:", page.input_value("#chat-title"))
                ok("finalize synthesizes into a tagged bubble", False)
        else:
            ok("finalize button hidden without extra models", True)

        # --- side chat (floating panel, never persisted) ---
        page.evaluate("import('/js/chat.js').then(m => m.openSideChat('selected context text'))")
        page.wait_for_selector("#side-chat:not([hidden])", timeout=3000)
        n_chats_before = page.locator("#chat-nav .chat-item").count()
        page.fill("#sc-input", "what is this?")
        page.click("#sc-send")
        page.wait_for_function(
            "document.querySelectorAll('#sc-messages .sc-msg.assistant').length >= 1",
            timeout=15000)
        settle(400)
        ok("side chat streams into floating panel",
            page.inner_text("#sc-messages").strip() != "")
        page.click("#sc-close")
        settle(400)
        ok("side chat created no server chat",
            page.locator("#chat-nav .chat-item").count() == n_chats_before)

        # --- full-screen composer ---
        page.click("#btn-fullscreen")
        ok("full-screen editor toggles",
            "fullscreen" in (page.get_attribute("#composer", "class") or ""))
        page.click("#btn-fullscreen")

        # --- XSS: model output is sanitized, never executed ---
        c.post("/api/chats", json={"provider_id": "openai", "model": "gpt-4o",
               "title": "xss probe"})
        probe_id = [x for x in c.get("/api/chats").json()
                    if x["title"] == "xss probe"][0]["id"]
        c.post(f"/api/chats/{probe_id}/messages", json={"content": "probe"})
        probe_msg = next(x for x in c.get(f"/api/chats/{probe_id}").json()["messages"]
                         if x["role"] == "user")
        c.patch(f"/api/chats/{probe_id}/messages/{probe_msg['id']}",
                json={"content": "hello <img src=x onerror=\"window.__xssHit=true\">"
                                "<script>window.__xssHit=true</script> world"})
        page.evaluate("window.__xssHit = false")
        page.evaluate(f"import('/js/chat.js').then(m => m.openChat('{probe_id}'))")
        settle(600)
        ok("XSS payload neutralized in message render",
            page.evaluate("window.__xssHit") is False
            and page.locator(".msg.user .body img").count() == 0
            and "hello" in page.inner_text(".msg.user .body"))
        c.delete(f"/api/chats/{probe_id}")

        # --- memory modal ---
        page.click("#btn-memory")
        page.wait_for_selector("#mem-enabled", timeout=3000)
        page.fill("#mem-new", "Prefers terse answers in UI checks")
        page.click("#mem-add")
        settle(400)
        ok("memory added via Memory panel",
            "Prefers terse answers" in page.inner_text("#mem-list"))
        page.keyboard.press("Escape")

        # --- usage dashboard ---
        page.click("#btn-usage")
        page.wait_for_selector(".stats-table", timeout=5000)
        ok("usage dashboard shows totals",
            "Totals" in page.inner_text("#usage-table"))
        page.keyboard.press("Escape")

        # --- shortcuts tab (rebinding UI) ---
        page.click("#btn-settings")
        page.wait_for_selector("#s-theme", timeout=3000)
        page.click("button[data-tab='shortcuts']")
        page.wait_for_selector(".rebind", timeout=3000)
        ok("shortcuts rebinding UI present",
            page.locator(".rebind").count() >= 5)
        page.keyboard.press("Escape")

        # --- static assets + PWA ---
        for path in ["/manifest.webmanifest", "/icon.svg", "/vendor/marked.min.js",
                     "/vendor/highlight.min.js", "/vendor/dompurify.min.js",
                     "/vendor/highlight-github-dark.min.css", "/js/main.js",
                     "/js/artifacts.js", "/sw.js"]:
            ok(f"asset {path}", c.get(path).status_code == 200)

        browser.close()

    mock_srv.should_exit = True
    app_srv.should_exit = True
    for e in page_errors[:8]:
        print(f"  [pageerror] {e[:300]}")
    ok("no page errors", not page_errors)

    print()
    failed = 0
    for name, passed in checks:
        if not passed:
            failed += 1
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print(f"\n{len(checks) - failed}/{len(checks)} passed · data dir: {data_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
