"""Test fixtures: isolated data dir, app client, mock provider wiring."""

import base64
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import app.db as db
from app.main import app, startup


class MockProvider:
    """Pure-ASGI mock speaking the OpenAI chat-completions contract."""

    def __init__(self):
        self.requests: list[dict] = []
        self.fail_with: tuple[int, str] | None = None

    async def __call__(self, scope, receive, send):
        body = b""
        while True:
            msg = await receive()
            if msg["type"] == "http.request":
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            elif msg["type"] == "http.disconnect":
                return
        data = json.loads(body or b"{}")
        self.requests.append(data)
        if scope["method"] == "GET" and scope["path"].endswith("/models"):
            payload = json.dumps({"data": [{"id": "mock-model-a"},
                                           {"id": "mock-model-b"}]}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": payload})
            return
        if scope["path"].endswith("/embeddings"):
            inputs = data.get("input") or []
            inputs = inputs if isinstance(inputs, list) else [inputs]
            vecs = [[1.0] * 8 for _ in inputs]  # identical vectors: cosine 1.0
            payload = json.dumps({"data": [{"index": i, "embedding": v}
                                           for i, v in enumerate(vecs)]}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": payload})
            return
        if scope["path"].endswith("/images/generations"):
            payload = json.dumps({"data": [
                {"b64_json": base64.b64encode(b"PNGDATA").decode()}]}).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": payload})
            return
        if self.fail_with:
            status, text = self.fail_with
            payload = json.dumps({"error": {"message": text}}).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": payload})
            return
        system_text = " ".join(m.get("content", "") for m in data.get("messages", [])
                               if isinstance(m.get("content"), str))
        if "extract durable facts" in system_text:
            reply = '["Prefers concise answers with code"]'
        elif data.get("stream"):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            for word in ["Hello", " ", "fuzzy", " ", "world"]:
                chunk = json.dumps({"choices": [{"delta": {"content": word}}]})
                await send({"type": "http.response.body", "more_body": True,
                            "body": f"data: {chunk}\n\n".encode()})
            await send({"type": "http.response.body", "body": b"data: [DONE]\n\n"})
            return
        else:
            reply = "Fuzzy Test Chat"
        payload = json.dumps({"choices": [{"message": {"content": reply}}]}).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": payload})


@pytest.fixture()
def mock_provider(tmp_path, monkeypatch):
    """Run the mock on a local port and point the builtin 'openai' row at it."""
    mock = MockProvider()
    from uvicorn import Config, Server

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = Server(Config(app=mock, host="127.0.0.1", port=port,
                           log_level="critical", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/any", timeout=0.2)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    # the real app reads provider rows per request — repoint the openai row
    con = db.connect()
    con.execute("UPDATE providers SET base_url=? WHERE id='openai'", (base,))
    con.commit()
    con.close()
    yield mock
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENTYPER_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENTYPER_FERNET_KEY", raising=False)
    db.DATA_DIR = tmp_path
    db.DB_PATH = tmp_path / "opentyper.db"
    startup()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def openai_key(client):
    r = client.put("/api/providers/openai/key", json={"api_key": "sk-test-1234567890"})
    assert r.status_code == 200
    return "openai"


@pytest.fixture()
def chat(client, openai_key):
    r = client.post("/api/chats", json={"model": "gpt-4o", "provider_id": "openai"})
    assert r.status_code == 200
    return r.json()["id"]
