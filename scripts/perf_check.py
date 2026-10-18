#!/usr/bin/env python3
"""Performance benchmark for the standing gates in docs/TODO.md:

  1. open a 500-message chat (render cap: newest 200 + "Load earlier")
  2. expand the cap ("Load earlier" → full render)
  3. stream-frame pacing during a mock SSE stream (rAF gap p50/p95/max +
     total long-task time)

Headless Chrome without a GPU cannot observe compositor vsync, so frame gaps
here measure MAIN-THREAD blocking (what would actually drop frames on real
hardware), which is the property the gate is about. Budgets are set loose
enough to be stable on a busy dev box; the numbers printed are the record.

Run: uv run --with playwright python scripts/perf_check.py
Exit 0 = within budget. """

import json
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CHROME = "/usr/bin/google-chrome"
N_SEED = 500
MSG_TEMPLATE = (
    "Message {i} — a **realistic** paragraph with `inline code` and a list:\n\n"
    "- one\n- two\n- three\n\n{maybe_code}And a closing line so lengths vary a bit. "
    "Padding text follows to land near a kilobyte per message. " * 3)
CODE_BLOCK = "```html\n<p>demo {i}</p>\n```\n\n"

BUDGETS = {"open_ms": 2500, "earlier_ms": 3000,
           "raf_p95_ms": 64, "stream_longtask_ms": 400}


class BurstProvider:
    """OpenAI contract; 'burst' prompts stream ~300 fast deltas so the UI has
    a sustained stream to pace against."""

    def __init__(self):
        self.requests = []

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            msg = await receive()
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
        data = json.loads(body)
        self.requests.append(data)
        reply = ("chunk-body " * 40).strip()  # ~300 deltas once split
        words = reply.split(" ")
        await __import__("asyncio").sleep(0)
        if data.get("stream"):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            for w in words:
                chunk = json.dumps({"choices": [{"delta": {"content": w + " "}}]})
                await send({"type": "http.response.body", "more_body": True,
                            "body": f"data: {chunk}\n\n".encode()})
                await __import__("asyncio").sleep(0.008)
            await send({"type": "http.response.body", "body": b"data: [DONE]\n\n"})
            return
        payload = json.dumps({"choices": [{"message": {"content": reply}}]}).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": payload})


def free_port() -> int:
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def serve(app, port, lifespan="off"):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="critical",
                                           lifespan=lifespan))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    return server


def main() -> int:
    import os

    from playwright.sync_api import sync_playwright

    data_dir = Path(tempfile.mkdtemp(prefix="opentyper-perf-"))
    os.environ["OPENTYPER_DATA_DIR"] = str(data_dir)
    os.chdir(ROOT)
    from app.main import app as ot_app

    mock = BurstProvider()
    mock_port = free_port()
    serve(mock, mock_port)
    app_port = free_port()
    app_srv = serve(ot_app, app_port, lifespan="on")
    base = f"http://127.0.0.1:{app_port}"
    for _ in range(100):
        try:
            if httpx.get(base + "/api/health", timeout=0.2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)

    c = httpx.Client(base_url=base, timeout=30)
    c.put("/api/providers/openai/key", json={"api_key": "sk-perf-key"})
    c.put("/api/providers/openai",
          json={"base_url": f"http://127.0.0.1:{mock_port}/v1"})
    chat = c.post("/api/chats", json={"model": "gpt-4o",
                                      "provider_id": "openai"}).json()

    # seed N_SEED messages straight into the temp DB (WAL; the app is idle)
    ts = time.time()
    with sqlite3.connect(data_dir / "opentyper.db") as con:
        for i in range(N_SEED):
            role = "user" if i % 2 == 0 else "assistant"
            body = MSG_TEMPLATE.format(i=i, maybe_code=""
                                       if i % 10 else CODE_BLOCK.format(i=i))
            con.execute(
                "INSERT INTO messages (id, chat_id, role, content, is_active, "
                "created_at) VALUES (?,?,?,?,1,?)",
                (f"seed-{i:04d}", chat["id"], role, body,
                 f"2026-10-18T12:{i // 60:02d}:{i % 60:02d}.000000+00:00"))
        con.commit()
    stamp = time.strftime("%H:%M", time.gmtime(ts))

    results, failures = {}, []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME, headless=True,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.goto(base)
        page.wait_for_selector("#model-select option", state="attached", timeout=8000)

        # 1 — open the 500-message chat (renders newest 200 + "Load earlier")
        page.evaluate("""window.__lt = 0;
            new PerformanceObserver(l => { for (const e of l.getEntries())
                window.__lt += e.duration; })
              .observe({ entryTypes: ['longtask'] });""")
        t0 = time.perf_counter()
        page.locator(".chat-item", has_text="New chat").first.click()
        page.wait_for_function(
            "document.querySelectorAll('#messages .msg').length >= 200",
            timeout=15000)
        results["open_ms"] = round((time.perf_counter() - t0) * 1000)
        results["open_longtask_ms"] = round(page.evaluate("window.__lt"))
        results["rendered_cap"] = page.locator("#messages .msg").count()

        # 2 — "Load earlier" expands the cap (full 500 render)
        t0 = time.perf_counter()
        page.locator("button.ghost", has_text="earlier message").click()
        page.wait_for_function(
            f"document.querySelectorAll('#messages .msg').length >= {N_SEED}",
            timeout=20000)
        results["earlier_ms"] = round((time.perf_counter() - t0) * 1000)
        results["rendered_full"] = page.locator("#messages .msg").count()

        # 3 — stream pacing: rAF gaps during a ~2.5s burst stream
        page.evaluate("""window.__gaps = []; window.__lt = 0;
            let last;
            const tick = t => { if (last !== undefined) window.__gaps.push(t - last);
                last = t; requestAnimationFrame(tick); };
            requestAnimationFrame(tick);""")
        page.fill("#input", f"burst please {stamp}")
        t0 = time.perf_counter()
        page.click("#btn-send")
        page.wait_for_function(
            "document.querySelectorAll('#messages .msg.assistant').length > 0 "
            "&& [...document.querySelectorAll('#messages .msg.assistant')]"
            ".pop().textContent.length > 300", timeout=30000)
        page.wait_for_timeout(600)  # let finalizeMd + refresh settle
        results["stream_ms"] = round((time.perf_counter() - t0) * 1000)
        gaps = page.evaluate("window.__gaps.slice(2)")
        results["raf_p50_ms"] = round(statistics.median(gaps), 1)
        results["raf_p95_ms"] = round(statistics.quantiles(gaps, n=20)[18], 1)
        results["raf_max_ms"] = round(max(gaps), 1)
        results["stream_longtask_ms"] = round(page.evaluate("window.__lt"))
        browser.close()

    app_srv.should_exit = True
    print(f"\n500-message chat benchmark (headless {CHROME.rsplit('/', 1)[-1]})")
    for k, v in results.items():
        print(f"  {k:22} {v}")
    for key, budget in BUDGETS.items():
        if results[key] > budget:
            failures.append(f"{key}={results[key]} > budget {budget}")
    if failures:
        print("\nOVER BUDGET: " + "; ".join(failures))
        return 1
    print(f"\nAll budgets met: {BUDGETS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
