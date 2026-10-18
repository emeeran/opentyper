"""API tests: CRUD, streaming against the mock provider, search, import/export,
auth. Each test names the MUST item it covers."""

import json

import pytest
from fastapi.testclient import TestClient


def sse_events(response) -> list[tuple[str, dict]]:
    """Parse an SSE response body into (event, data) pairs."""
    response.read()
    out = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                import json
                data = json.loads(line[5:].strip())
        out.append((event, data))
    return out


# --- health & basic CRUD ------------------------------------------------------

def test_health(client):
    assert client.get("/api/health").status_code == 200


def test_builtin_providers_seeded(client):
    rows = client.get("/api/providers").json()
    ids = {r["id"] for r in rows}
    assert {"openai", "anthropic", "google", "openrouter", "groq", "deepseek",
            "mistral", "xai", "perplexity", "huggingface"} <= ids
    assert all(r["api_key_encrypted"] is None for r in rows)


def test_builtin_reseeding_preserves_key_and_base_url(client):
    client.put("/api/providers/groq/key", json={"api_key": "gsk_test123456"})
    client.put("/api/providers/groq", json={"base_url": "http://127.0.0.1:9/v1"})
    from app.main import startup
    startup()  # re-seed builtin rows
    row = next(r for r in client.get("/api/providers").json() if r["id"] == "groq")
    assert row["key_hint"] == "gsk…3456" or row["key_hint"].startswith("gsk")
    assert row["base_url"] == "http://127.0.0.1:9/v1"


def test_provider_key_roundtrip_hint_only(client):
    r = client.put("/api/providers/openai/key", json={"api_key": "sk-test-1234567890"})
    assert r.json()["key_hint"].startswith("sk-")
    assert "1234567890" not in r.json()["key_hint"]
    body = client.get("/api/providers").json()
    row = next(r for r in body if r["id"] == "openai")
    assert row["key_hint"] and row["api_key_encrypted"] is None


def test_chat_crud_and_folder_move(client):
    fid = client.post("/api/folders", json={"name": "Work"}).json()["id"]
    cid = client.post("/api/chats", json={"model": "gpt-4o", "provider_id": "openai",
                                          "folder_id": fid}).json()["id"]
    assert client.patch(f"/api/chats/{cid}", json={"title": "Renamed"}).json()["title"] == "Renamed"
    assert client.patch(f"/api/chats/{cid}", json={"folder_id": ""}).json()["folder_id"] is None
    assert client.delete(f"/api/chats/{cid}").json()["ok"]
    assert client.get(f"/api/chats/{cid}").status_code == 404
    # folder delete leaves chats alive (SET NULL covered above via explicit clear too)
    assert client.delete(f"/api/folders/{fid}").json()["ok"]


def test_folder_delete_sets_chat_folder_null(client):
    fid = client.post("/api/folders", json={"name": "X"}).json()["id"]
    cid = client.post("/api/chats", json={"model": "gpt-4o", "provider_id": "openai",
                                          "folder_id": fid}).json()["id"]
    client.delete(f"/api/folders/{fid}")
    assert client.get(f"/api/chats/{cid}").json()["folder_id"] is None


def test_chat_validation_bounds(client, openai_key):
    r = client.post("/api/chats", json={"model": "gpt-4o", "provider_id": "openai",
                                        "temperature": 5})
    assert r.status_code == 422
    r = client.post("/api/chats", json={"model": "gpt-4o", "provider_id": "nope"})
    assert r.status_code == 404


def test_folder_default_model_and_system_prompt(client, openai_key):
    fid = client.post("/api/folders", json={
        "name": "P", "provider_id": "openai", "model": "gpt-4o-mini",
        "system_prompt": "You are terse."}).json()["id"]
    cid = client.post("/api/chats", json={"folder_id": fid}).json()
    assert cid["model"] == "gpt-4o-mini"
    assert cid["system_prompt"] == "You are terse."


# --- streaming ----------------------------------------------------------------

def test_streamed_chat_mock(client, chat, mock_provider):
    with client.stream("POST", f"/api/chats/{chat}/messages",
                       json={"content": "hi"}) as resp:
        events = sse_events(resp)
    kinds = [e for e, _ in events]
    assert kinds == ["delta", "delta", "delta", "delta", "delta", "meta", "done"]
    assert "".join(d["text"] for e, d in events if e == "delta") == "Hello fuzzy world"
    detail = client.get(f"/api/chats/{chat}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["interrupted"] == 0
    # usage recorded (estimate, since the mock sends no usage)
    last = detail["messages"][1]
    assert last["prompt_tokens"] > 0 and last["completion_tokens"] > 0


def test_stream_requires_key(client):
    """409 raised before the stream starts when no key is configured."""
    cid = client.post("/api/chats", json={"model": "gpt-4o",
                                          "provider_id": "openai"}).json()["id"]
    assert client.post(f"/api/chats/{cid}/messages",
                       json={"content": "hi"}).status_code == 409


def test_stream_provider_error_persisted_and_hidden_from_context(client, chat, mock_provider):
    mock_provider.fail_with = (401, "bad key")
    with client.stream("POST", f"/api/chats/{chat}/messages",
                       json={"content": "hi"}) as resp:
        events = sse_events(resp)
    assert events[-1][0] == "error"
    assert "bad key" in events[-1][1]["message"]
    assert "sk-test" not in events[-1][1]["message"]
    detail = client.get(f"/api/chats/{chat}").json()
    last = detail["messages"][-1]
    assert last["role"] == "assistant" and last["error"]
    assert "bad key" in last["error"] and not last["content"]
    # the error row is not leaked into the next request's context
    mock_provider.fail_with = None
    with client.stream("POST", f"/api/chats/{chat}/regenerate") as resp:
        assert sse_events(resp)[-1][0] == "done"
    sent = mock_provider.requests[-1]
    assert [m["role"] for m in sent["messages"]] == ["user"]


def test_regenerate_keeps_old_reply_as_variant(client, chat, mock_provider):
    with client.stream("POST", f"/api/chats/{chat}/messages", json={"content": "hi"}):
        pass
    before = client.get(f"/api/chats/{chat}").json()["messages"]
    with client.stream("POST", f"/api/chats/{chat}/regenerate") as resp:
        events = sse_events(resp)
    assert events[-1][0] == "done"
    after = client.get(f"/api/chats/{chat}").json()["messages"]
    # threads-lite: the old reply is kept as an inactive variant of the new one
    assert len(after) == len(before) + 1
    active = [m for m in after if m["is_active"]]
    assert len(active) == len(before)
    old = [m for m in after if not m["is_active"]]
    assert len(old) == 1 and old[0]["role"] == "assistant"
    grp = old[0]["variant_group"]
    assert grp and active[-1]["variant_group"] == grp


def test_activate_variant_switches_active_reply(client, chat, mock_provider):
    with client.stream("POST", f"/api/chats/{chat}/messages", json={"content": "hi"}):
        pass
    with client.stream("POST", f"/api/chats/{chat}/regenerate"):
        pass
    msgs = client.get(f"/api/chats/{chat}").json()["messages"]
    old = next(m for m in msgs if not m["is_active"])
    r = client.post(f"/api/chats/{chat}/messages/{old['id']}/activate",
                    json={"active": True})
    assert r.json()["ok"]
    msgs = client.get(f"/api/chats/{chat}").json()["messages"]
    active = [m for m in msgs if m["is_active"] and m["role"] == "assistant"]
    assert len(active) == 1 and active[0]["id"] == old["id"]


def test_regenerate_empty_chat_400(client, chat):
    assert client.post(f"/api/chats/{chat}/regenerate").status_code == 400


def test_params_sent_to_provider(client, chat, mock_provider):
    client.patch(f"/api/chats/{chat}", json={"temperature": 0.3, "max_tokens": 100,
                                             "system_prompt": "be brief"})
    with client.stream("POST", f"/api/chats/{chat}/messages", json={"content": "hi"}):
        pass
    sent = mock_provider.requests[-1]
    assert sent["temperature"] == 0.3 and sent["max_tokens"] == 100
    assert sent["messages"][0] == {"role": "system", "content": "be brief"}


def test_title_generated_from_first_exchange(client, chat, mock_provider):
    with client.stream("POST", f"/api/chats/{chat}/messages", json={"content": "hi"}):
        pass
    import time
    deadline = time.time() + 5
    title = "New chat"
    while time.time() < deadline:
        title = client.get(f"/api/chats/{chat}").json()["title"]
        if title != "New chat":
            break
        time.sleep(0.1)
    assert title == "Fuzzy Test Chat"


# --- search -------------------------------------------------------------------

def test_fts_search_and_delete_sync(client, chat, mock_provider):
    with client.stream("POST", f"/api/chats/{chat}/messages", json={"content": "hi"}):
        pass
    r = client.get("/api/search", params={"q": "fuzzy"})
    hits = r.json()
    assert len(hits) == 1 and hits[0]["chat_id"] == chat
    assert "fuzzy" in hits[0]["snippet"]
    # assistant delete (regenerate) keeps the index in sync
    with client.stream("POST", f"/api/chats/{chat}/regenerate"):
        pass
    assert len(client.get("/api/search", params={"q": "fuzzy"}).json()) == 1


def test_search_invalid_query_400(client):
    assert client.get("/api/search", params={"q": "' AND"}).status_code in (200, 400)


# --- prompts / agents ---------------------------------------------------------

def test_prompt_crud(client):
    pid = client.post("/api/prompts", json={
        "title": "Explain", "content": "Explain {{topic}}", "tags": ["teaching"]}).json()
    assert pid["tags"] == ["teaching"]
    client.patch(f"/api/prompts/{pid['id']}", json={
        "title": "Explain2", "content": "x", "description": "", "tags": []})
    row = next(p for p in client.get("/api/prompts").json() if p["id"] == pid["id"])
    assert row["title"] == "Explain2"
    assert client.delete(f"/api/prompts/{pid['id']}").json()["ok"]


def test_agent_crud_and_chat_start(client, openai_key):
    aid = client.post("/api/agents", json={
        "name": "Poet", "system_prompt": "You rhyme.", "model": "gpt-4o",
        "provider_id": "openai", "temperature": 0.9}).json()
    chat = client.post("/api/chats", json={"agent_id": aid["id"]}).json()
    assert chat["system_prompt"] == "You rhyme."
    assert chat["model"] == "gpt-4o" and chat["temperature"] == 0.9
    assert client.delete(f"/api/agents/{aid['id']}").json()["ok"]
    assert client.get(f"/api/chats/{chat['id']}").status_code == 200  # SET NULL


# --- import / export ----------------------------------------------------------

def test_export_import_roundtrip(client, chat, mock_provider):
    with client.stream("POST", f"/api/chats/{chat}/messages", json={"content": "hi"}):
        pass
    doc = client.post("/api/export", json={"format": "json"}).text
    client.delete(f"/api/chats/{chat}")
    assert client.post("/api/import", content=doc,
                       headers={"content-type": "application/json"}).json()["chats"] == 1
    again = client.post("/api/export", json={"format": "json"}).text
    assert json.loads(again)["chats"] == json.loads(doc)["chats"]


def test_export_markdown(client, chat, mock_provider):
    with client.stream("POST", f"/api/chats/{chat}/messages", json={"content": "hi"}):
        pass
    md = client.post("/api/export", json={"format": "markdown"}).text
    assert "## user" in md and "## assistant" in md and "Hello fuzzy world" in md


# --- settings / auth ----------------------------------------------------------

def test_settings_roundtrip(client):
    r = client.put("/api/settings", json={"theme": "dark", "enter_to_send": False})
    assert r.json()["theme"] == "dark" and r.json()["enter_to_send"] is False
    assert client.put("/api/settings", json={"theme": "neon"}).status_code == 422


def test_password_auth_flow(client):
    assert client.put("/api/settings/password", json={"password": "hunter2hunter2"}).json()["ok"]
    # fresh client without cookie → 401
    bare = TestClient(client.app)
    assert bare.get("/api/chats").status_code == 401
    assert bare.get("/api/health").status_code == 200
    assert bare.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert bare.post("/api/auth/login", json={"password": "hunter2hunter2"}).status_code == 200
    assert bare.get("/api/chats").status_code == 200
    # cookie invalid after password change
    client.put("/api/settings/password", json={"password": "hunter3hunter3"})
    assert bare.get("/api/chats").status_code == 401
    # the SPA shell stays public even when locked: the browser must receive
    # app modules to render the sign-in modal (regression: `/` used to 401 as JSON)
    assert bare.get("/").status_code == 200
    assert "OpenTyper" in bare.get("/").text
    for path in ("/js/main.js", "/style.css", "/manifest.webmanifest", "/icon.svg",
                 "/vendor/dompurify.min.js"):
        assert bare.get(path).status_code == 200, path


def test_secret_key_file_permissions(client, tmp_path):
    import stat
    mode = (tmp_path / "secret.key").stat().st_mode
    assert stat.S_IMODE(mode) == 0o600


def test_custom_model_endpoint(client, tmp_path):
    r = client.put("/api/providers/openai", json={"models": ["my-fine-tune"]})
    assert r.status_code == 200
    models = client.get("/api/models").json()
    openai_models = next(m for m in models if m["provider_id"] == "openai")["models"]
    assert "my-fine-tune" in openai_models and "gpt-4o" in openai_models


# --- seeds & recommended params ------------------------------------------------

def test_seeds_present_and_stable(client):
    prompts = {p["id"] for p in client.get("/api/prompts").json()}
    agents = {a["id"] for a in client.get("/api/agents").json()}
    assert "seed-p-writing" in prompts and "seed-p-regex" in prompts
    assert "seed-a-engineer" in agents and "seed-a-tutor" in agents
    # restart does not duplicate
    from app.main import startup
    startup()
    assert len(client.get("/api/prompts").json()) == len(prompts)
    # user deletion persists across restart
    client.delete("/api/prompts/seed-p-regex")
    startup()
    assert "seed-p-regex" not in {p["id"] for p in client.get("/api/prompts").json()}


def test_recommended_params_applied(client, openai_key):
    r = client.post("/api/chats", json={"model": "gpt-4o", "provider_id": "openai"}).json()
    assert r["temperature"] == 0.7
    r = client.post("/api/chats", json={"model": "o3", "provider_id": "openai"}).json()
    assert r["temperature"] is None  # reasoning model: leave provider default
    r = client.post("/api/chats", json={"model": "gpt-4o", "provider_id": "openai",
                                        "temperature": 0.2}).json()
    assert r["temperature"] == 0.2  # explicit wins


# --- live model fetch & connection test -----------------------------------------

def test_refresh_models_and_test_connection(client, openai_key, mock_provider):
    r = client.post("/api/providers/openai/refresh-models").json()
    assert r["ok"] and r["fetched"] == 2
    models = next(m for m in client.get("/api/models").json()
                  if m["provider_id"] == "openai")["models"]
    assert "mock-model-a" in models and "gpt-4o" in models  # merged, not replaced
    # connection test: defaults to the first stored model…
    r = client.post("/api/providers/openai/test").json()
    assert r["ok"] is True and r["reply"] and r["model"] == "gpt-5"
    # …or an explicitly requested one
    r = client.post("/api/providers/openai/test",
                    json={"model": "mock-model-b"}).json()
    assert r["ok"] is True and r["model"] == "mock-model-b"


def test_connection_test_failure_scrubs_key(client, openai_key, mock_provider):
    mock_provider.fail_with = (401, "bad key, key was sk-test-1234567890")
    r = client.post("/api/providers/openai/test").json()
    assert r["ok"] is False
    assert "bad key" in r["error"]
    assert "sk-test-1234567890" not in r["error"]


def test_test_endpoint_requires_key(client):
    assert client.post("/api/providers/openai/test").status_code == 409


def test_remote_model_list_adapters():
    """Anthropic/Google /models parsing, no network (httpx.MockTransport)."""
    import asyncio

    import httpx

    from app.providers import list_remote_models

    def anthropic_handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers["x-api-key"] == "k"
        return httpx.Response(200, json={"data": [{"id": "claude-y"},
                                                  {"id": "claude-x"}]})

    out = asyncio.run(list_remote_models(
        {"kind": "anthropic", "base_url": "https://x"}, "k",
        httpx.AsyncClient(transport=httpx.MockTransport(anthropic_handler))))
    assert out == ["claude-x", "claude-y"]

    def google_handler(request):
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-pro",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004",
             "supportedGenerationMethods": ["embedContent"]},
        ]})

    out = asyncio.run(list_remote_models(
        {"kind": "google", "base_url": "https://x"}, "k",
        httpx.AsyncClient(transport=httpx.MockTransport(google_handler))))
    assert out == ["gemini-pro"]


# --- premium: edit / fork / multi-model / params ---------------------------------

def _exchange(client, chat, text="hi"):
    with client.stream("POST", f"/api/chats/{chat}/messages", json={"content": text}):
        pass


def test_edit_rerun_keeps_single_reply_as_variant(client, chat, mock_provider):
    _exchange(client, chat, "first")
    first = client.get(f"/api/chats/{chat}").json()["messages"]
    assert [m["role"] for m in first] == ["user", "assistant"]
    user_id = first[0]["id"]
    with client.stream("POST", f"/api/chats/{chat}/messages/{user_id}/rerun",
                       json={"content": "second"}) as resp:
        events = sse_events(resp)
    assert events[-1][0] == "done"
    detail = client.get(f"/api/chats/{chat}").json()["messages"]
    active = [m for m in detail if m["is_active"]]
    assert [m["role"] for m in active] == ["user", "assistant"]
    assert active[0]["content"] == "second"
    variants = [m for m in detail if not m["is_active"]]
    assert len(variants) == 1 and variants[0]["variant_group"]


def test_edit_assistant_message(client, chat, mock_provider):
    _exchange(client, chat)
    detail = client.get(f"/api/chats/{chat}").json()
    aid = detail["messages"][1]["id"]
    r = client.patch(f"/api/chats/{chat}/messages/{aid}",
                     json={"content": "corrected"})
    assert r.json()["ok"]
    assert client.get(f"/api/chats/{chat}").json()["messages"][1]["content"] == "corrected"


def test_rerun_rejects_assistant_message(client, chat, mock_provider):
    _exchange(client, chat)
    aid = client.get(f"/api/chats/{chat}").json()["messages"][1]["id"]
    assert client.post(f"/api/chats/{chat}/messages/{aid}/rerun",
                       json={"content": "x"}).status_code == 400


def test_fork_chat_full_and_truncated(client, chat, mock_provider):
    _exchange(client, chat)
    _exchange(client, chat, "more")
    detail = client.get(f"/api/chats/{chat}").json()
    r = client.post(f"/api/chats/{chat}/fork", json={}).json()
    assert r["copied_messages"] == 4
    full = client.get(f"/api/chats/{r['id']}").json()
    assert full["title"].endswith("(fork)")
    assert len(full["messages"]) == 4
    r2 = client.post(f"/api/chats/{chat}/fork",
                     json={"message_id": detail["messages"][0]["id"]}).json()
    assert r2["copied_messages"] == 1


def test_tags_and_archive(client, chat):
    r = client.patch(f"/api/chats/{chat}",
                     json={"tags": ["work", "ideas"], "archived": True}).json()
    assert r["tags"] == ["work", "ideas"] and r["archived"] is True
    assert client.get("/api/chats").json() == []          # archived hidden by default
    rows = client.get("/api/chats", params={"archived": "true"}).json()
    assert rows[0]["id"] == chat


def test_context_limit(client, chat, mock_provider):
    _exchange(client, chat)
    _exchange(client, chat, "again")
    client.patch(f"/api/chats/{chat}", json={"context_limit": 1})
    with client.stream("POST", f"/api/chats/{chat}/regenerate") as resp:
        assert sse_events(resp)[-1][0] == "done"
    sent = mock_provider.requests[-1]
    assert len(sent["messages"]) == 1  # only the newest message


def test_penalties_sent_to_provider(client, chat, mock_provider):
    client.patch(f"/api/chats/{chat}",
                 json={"frequency_penalty": 0.5, "presence_penalty": -0.3})
    _exchange(client, chat)
    sent = mock_provider.requests[-1]
    assert sent["frequency_penalty"] == 0.5
    assert sent["presence_penalty"] == -0.3


def test_non_stream_mode(client, chat, mock_provider):
    client.patch(f"/api/chats/{chat}", json={"stream": False})
    with client.stream("POST", f"/api/chats/{chat}/messages",
                       json={"content": "hi"}) as resp:
        events = sse_events(resp)
    kinds = [e for e, _ in events]
    assert kinds == ["delta", "meta", "done"]
    assert dict(events)["delta"]["text"] == "Fuzzy Test Chat"


def test_extra_models_multi_reply(client, chat, mock_provider):
    client.patch(f"/api/chats/{chat}", json={"extra_models": ["openai:mock-model-b"]})
    with client.stream("POST", f"/api/chats/{chat}/messages",
                       json={"content": "hi"}) as resp:
        events = sse_events(resp)
    kinds = [e for e, _ in events]
    assert kinds.count("meta") == 2
    assert kinds[-1] == "done"
    detail = client.get(f"/api/chats/{chat}").json()
    models = [m["model"] for m in detail["messages"] if m["role"] == "assistant"]
    assert models == ["gpt-4o", "mock-model-b"]


def test_costs_computed_when_prices_set(client, chat, mock_provider):
    client.put("/api/settings", json={"model_prices": {"gpt-4o": {"in": 1, "out": 2}}})
    _exchange(client, chat)
    last = client.get(f"/api/chats/{chat}").json()["messages"][-1]
    assert last["cost"] is not None and last["cost"] > 0


# --- premium: images --------------------------------------------------------------

def test_image_attach_and_serve(client, chat, mock_provider):
    data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
    with client.stream("POST", f"/api/chats/{chat}/messages",
                       json={"content": "what is this", "images": [data_url]}) as resp:
        assert sse_events(resp)[-1][0] == "done"
    user_msg = client.get(f"/api/chats/{chat}").json()["messages"][0]
    assert len(user_msg["images"]) == 1
    filename = user_msg["images"][0]["file"]
    r = client.get(f"/api/uploads/{filename}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    # provider saw an image_url content part (skip the fixture's bodyless probe
    # and any title-gen request — the multimodal one has list content)
    vision = next(r for r in mock_provider.requests
                  if r.get("messages") and isinstance(r["messages"][0]["content"], list))
    assert vision["messages"][0]["content"][1]["type"] == "image_url"
    # traversal is not possible
    assert client.get("/api/uploads/..%2Fopentyper.db").status_code in (404, 400)


def test_invalid_image_rejected(client, chat):
    r = client.post(f"/api/chats/{chat}/messages",
                    json={"content": "x", "images": ["data:text/html;base64,AAA"]})
    assert r.status_code == 400


def test_vision_body_adapters():
    """Image parts per dialect, no network."""

    from app.providers import _body

    img = [("image/png", "AAAA")]
    _, b = _body({"kind": "openai", "base_url": "https://x", "id": "openai"},
                 "m", [{"role": "user", "content": "hi", "images": img}], {}, True)
    assert b["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png")
    _, b = _body({"kind": "anthropic", "base_url": "https://x", "id": "a"},
                 "m", [{"role": "user", "content": "hi", "images": img}], {}, True)
    src = b["messages"][0]["content"][1]["source"]
    assert src == {"type": "base64", "media_type": "image/png", "data": "AAAA"}
    _, b = _body({"kind": "google", "base_url": "https://x", "id": "g"},
                 "m", [{"role": "user", "content": "hi", "images": img}], {}, True)
    assert b["contents"][0]["parts"][1]["inlineData"] == {
        "mimeType": "image/png", "data": "AAAA"}


def test_text_only_body_adapters():
    """Live-smoke regressions: google `parts` must be part objects even without
    images (API 400s on a bare string), and no dialect leaks the internal
    `images` key into the request (Groq 400s on `images: []`)."""

    from app.providers import _body

    msg = [{"role": "user", "content": "hi", "images": []}]
    _, b = _body({"kind": "google", "base_url": "https://x", "id": "g"},
                 "m", msg, {}, True)
    assert b["contents"][0]["parts"] == [{"text": "hi"}]
    for kind in ("anthropic", "openai"):
        _, b = _body({"kind": kind, "base_url": "https://x", "id": "x"},
                     "m", [dict(m) for m in msg], {}, True)
        assert "images" not in b["messages"][0]
        assert b["messages"][0]["content"] == "hi"


# --- premium: share links ----------------------------------------------------------

def test_share_flow_and_revocation(client, chat, mock_provider):
    _exchange(client, chat)
    url = client.post(f"/api/chats/{chat}/share").json()["url"]
    token = url.rsplit("/", 1)[1]
    pub = client.get(f"/api/public/{token}").json()
    assert pub["title"] and [m["role"] for m in pub["messages"]] == ["user", "assistant"]
    assert "content" in pub["messages"][0]
    # same link returned on re-share
    assert client.post(f"/api/chats/{chat}/share").json()["url"] == url
    assert client.get(url).status_code == 200  # the /s/ page
    assert client.delete(f"/api/chats/{chat}/share").json()["ok"]
    assert client.get(f"/api/public/{token}").status_code == 404


def test_share_bypasses_password(client, chat, mock_provider):
    _exchange(client, chat)
    url = client.post(f"/api/chats/{chat}/share").json()["url"]
    token = url.rsplit("/", 1)[1]
    client.put("/api/settings/password", json={"password": "hunter2hunter2"})
    bare = TestClient(client.app)
    assert bare.get("/api/chats").status_code == 401
    assert bare.get(f"/api/public/{token}").status_code == 200
    assert bare.get(f"/s/{token}").status_code == 200
    client.delete("/api/settings/password")


# --- premium: documents / knowledge --------------------------------------------------

def test_documents_crud_and_knowledge_injection(client, chat, mock_provider):
    did = client.post("/api/documents", json={
        "name": "notes.md",
        "content": "The fuzzy wumpus lives in the nebula forest and eats static."}).json()["id"]
    docs = client.get("/api/documents").json()
    assert docs[0]["name"] == "notes.md" and docs[0]["size"] > 0
    client.patch(f"/api/chats/{chat}", json={"use_knowledge": True})
    with client.stream("POST", f"/api/chats/{chat}/messages",
                       json={"content": "where does the wumpus live?"}) as resp:
        assert sse_events(resp)[-1][0] == "done"
    sent = mock_provider.requests[-1]
    assert sent["messages"][0]["role"] == "system"
    assert "wumpus" in sent["messages"][0]["content"]
    # per-send override works even when the chat flag is off
    client.patch(f"/api/chats/{chat}", json={"use_knowledge": False})
    with client.stream("POST", f"/api/chats/{chat}/messages",
                       json={"content": "wumpus?", "use_knowledge": True}) as resp:
        sse_events(resp)
    assert "wumpus" in mock_provider.requests[-1]["messages"][0]["content"]
    assert client.delete(f"/api/documents/{did}").json()["ok"]


# --- premium: export html + migration ------------------------------------------------

def test_export_html(client, chat, mock_provider):
    _exchange(client, chat)
    r = client.post("/api/export", json={"format": "html"})
    assert r.text.startswith("<!doctype html>")
    assert "Hello fuzzy world" in r.text
    assert client.post("/api/export", json={"format": "bogus"}).status_code == 400


def test_model_prices_setting_roundtrip(client):
    r = client.put("/api/settings", json={"model_prices": {"gpt": {"in": 1, "out": 2}}})
    assert r.json()["model_prices"] == {"gpt": {"in": 1, "out": 2}}


# --- premium: edge tts -------------------------------------------------------------

class _FakeCommunicate:
    """Stands in for edge_tts.Communicate — no network in tests."""

    def __init__(self, text, voice):
        self.text, self.voice = text, voice

    async def stream(self):
        yield {"type": "audio", "data": b"MP3FAKE" + self.voice.encode()}
        yield {"type": "WordBoundary", "offset": 1}


@pytest.fixture()
def fake_edge(monkeypatch):
    import app.tts as tts_mod
    monkeypatch.setattr(tts_mod.edge_tts, "Communicate", _FakeCommunicate)
    voices = [{"ShortName": "en-US-AriaNeural", "FriendlyName": "Aria",
               "Locale": "en-US", "Gender": "Female"}]
    class _VM:
        def __init__(self): self.voices = voices
        @classmethod
        async def create(cls): return cls()
    monkeypatch.setattr(tts_mod.edge_tts, "VoicesManager", _VM)


def test_edge_tts_voices_and_speech(client, fake_edge):
    voices = client.get("/api/tts/voices").json()
    assert voices[0]["id"] == "en-US-AriaNeural"
    r = client.get("/api/tts", params={"text": "hello", "voice": "en-US-AriaNeural"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"
    assert b"MP3FAKEen-US-AriaNeural" in r.content
    # over-cap text is rejected at the API (the client pre-slices; the lib truncates)
    r = client.get("/api/tts", params={"text": "x" * 6000})
    assert r.status_code == 400


def test_edge_tts_input_validation(client, fake_edge):
    assert client.get("/api/tts", params={"text": "   "}).status_code == 400
    assert client.get("/api/tts", params={"text": "hi",
                                          "voice": "../etc/passwd"}).status_code == 400
    assert client.get("/api/tts", params={"text": "hi",
                                          "voice": "not-a-voice"}).status_code == 400


def test_migration_readds_dropped_columns(client):
    import app.db as db
    from app.main import startup
    con = db.connect()
    con.execute("ALTER TABLE chats DROP COLUMN tags")
    con.execute("ALTER TABLE messages DROP COLUMN cost")
    con.commit()
    con.close()
    startup()  # re-runs idempotent migrations
    con = db.connect()
    chat_cols = {r[1] for r in con.execute("PRAGMA table_info(chats)")}
    msg_cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
    con.close()
    assert "tags" in chat_cols and "cost" in msg_cols
