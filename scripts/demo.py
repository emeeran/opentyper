#!/usr/bin/env python3
"""End-to-end demo: starts a mock OpenAI-compatible provider on a random port,
boots the OpenTyper app on another, configures a key + chat, streams one reply,
and prints the SSE events. Exit code 0 = all checks passed.

Runs in the project environment (`uv run scripts/demo.py`) — no inline script
metadata, because the app itself is the dependency."""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class MockProvider:
    """OpenAI chat-completions contract; base path '/v1' to mimic reality."""

    async def __call__(self, scope, receive, send):
        assert scope["type"] == "http"
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
        assert scope["method"] == "POST"
        auth = dict(scope["headers"]).get(b"authorization", b"")
        assert auth == b"Bearer sk-demo-key", auth  # key must arrive server-side
        data = json.loads(body)
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        reply = f"Demo reply to: {data['messages'][-1]['content'][:40]}"
        for word in reply.split(" "):
            chunk = json.dumps({"choices": [{"delta": {"content": word + " "}}]})
            await send({"type": "http.response.body", "more_body": True,
                        "body": f"data: {chunk}\n\n".encode()})
        await send({"type": "http.response.body", "body": b"data: [DONE]\n\n"})


def serve(app, lifespan="off") -> tuple[int, uvicorn.Server]:
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="critical", lifespan=lifespan))
    threading.Thread(target=server.run, daemon=True).start()
    return port, server


def main() -> int:
    import os
    import tempfile

    data_dir = Path(tempfile.mkdtemp(prefix="opentyper-demo-"))
    os.environ["OPENTYPER_DATA_DIR"] = str(data_dir)

    mock_port, mock_srv = serve(MockProvider())
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{mock_port}/health", timeout=0.2)
            break
        except httpx.HTTPError:
            time.sleep(0.05)

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
    checks = []

    r = c.put("/api/providers/openai/key", json={"api_key": "sk-demo-key"})
    checks.append(("provider key stored (hint only)", r.json()["key_hint"].startswith("sk-")))

    c.put("/api/providers/openai", json={"base_url": f"http://127.0.0.1:{mock_port}/v1"})

    cid = c.post("/api/chats", json={"model": "gpt-4o", "provider_id": "openai",
                                     "system_prompt": "You are a demo assistant."}).json()["id"]

    frames = []
    with c.stream("POST", f"/api/chats/{cid}/messages",
                  json={"content": "Hello OpenTyper!"}) as resp:
        checks.append(("stream HTTP 200 + SSE content type",
                       resp.status_code == 200
                       and resp.headers["content-type"].startswith("text/event-stream")))
        for line in resp.iter_lines():
            if line.startswith("event:") or line.startswith("data:"):
                frames.append(line)
    deltas = [json.loads(line[5:]) for line in frames
              if line.startswith("data:") and line != "data: [DONE]"]
    text = "".join(d.get("text", "") for d in deltas)
    checks.append(("streamed deltas reassembled",
                   text.startswith("Demo reply to: Hello OpenTyper")))
    checks.append(("done frame received", any("done" in line for line in frames)))

    detail = c.get(f"/api/chats/{cid}").json()
    checks.append(("user+assistant persisted",
                   [m["role"] for m in detail["messages"]] == ["user", "assistant"]))
    checks.append(("markdown export contains reply",
                   "Demo reply" in c.post("/api/export",
                                          json={"format": "markdown"}).text))

    md = c.get("/", follow_redirects=False)
    checks.append(("SPA served at /", md.status_code in (200, 307)))

    mock_srv.should_exit = True
    app_srv.should_exit = True
    print()
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok &= passed
    print(f"\ndata dir: {data_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
