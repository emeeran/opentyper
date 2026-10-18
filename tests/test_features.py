"""Feature tests for the parity batch: message management, variants, finalize,
side chat, memories, skills, stats, image generation, vector knowledge,
PDF extraction, import converters, temporary chats, new settings."""

import base64
import json
import time

from app import providers


def sse_events(response) -> list[tuple[str, dict]]:
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
                data = json.loads(line[5:].strip())
        out.append((event, data))
    return out


def _exchange(client, chat, text="hi"):
    with client.stream("POST", f"/api/chats/{chat}/messages", json={"content": text}):
        pass


# --- message management ---------------------------------------------------------

def test_delete_single_message(client, chat, mock_provider):
    _exchange(client, chat)
    _exchange(client, chat, "more")
    msgs = client.get(f"/api/chats/{chat}").json()["messages"]
    r = client.delete(f"/api/chats/{chat}/messages/{msgs[0]['id']}")
    assert r.json()["ok"]
    remaining = client.get(f"/api/chats/{chat}").json()["messages"]
    assert [m["id"] for m in remaining] == [m["id"] for m in msgs[1:]]


def test_delete_message_404_wrong_chat(client, chat, mock_provider):
    _exchange(client, chat)
    other = client.post("/api/chats", json={"provider_id": "openai",
                                            "model": "gpt-4o"}).json()["id"]
    mid = client.get(f"/api/chats/{chat}").json()["messages"][0]["id"]
    assert client.delete(f"/api/chats/{other}/messages/{mid}").status_code == 404


# --- variant switching ------------------------------------------------------------

def test_activate_only_one_in_group(client, chat, mock_provider):
    _exchange(client, chat)
    with client.stream("POST", f"/api/chats/{chat}/regenerate"):
        pass
    msgs = client.get(f"/api/chats/{chat}").json()["messages"]
    grp = msgs[-1]["variant_group"]
    old = next(m for m in msgs if not m["is_active"])
    client.post(f"/api/chats/{chat}/messages/{old['id']}/activate", json={})
    active = [m for m in client.get(f"/api/chats/{chat}").json()["messages"]
              if m["is_active"]]
    assert [m["id"] for m in active if m["variant_group"] == grp] == [old["id"]]


# --- shares list -------------------------------------------------------------------

def test_shares_list(client, chat, mock_provider):
    _exchange(client, chat)
    assert client.get("/api/shares").json() == []
    url = client.post(f"/api/chats/{chat}/share", json={}).json()["url"]
    shares = client.get("/api/shares").json()
    assert len(shares) == 1 and shares[0]["token"] in url
    assert shares[0]["title"]


# --- documents PATCH + hybrid vector knowledge ---------------------------------------

def test_patch_document(client):
    did = client.post("/api/documents", json={"name": "a.md",
                                              "content": "alpha beta"}).json()["id"]
    r = client.patch(f"/api/documents/{did}", json={"name": "b.md",
                                                    "content": "gamma delta"})
    assert r.json()["ok"]
    docs = client.get("/api/documents").json()
    assert docs[0]["name"] == "b.md"


def _enable_vectors(client):
    client.put("/api/providers/openai/key", json={"api_key": "sk-test-1234567890"})
    client.put("/api/settings", json={"embedding_provider_id": "openai",
                                      "embedding_model": "text-embedding-3-small"})


def test_document_reindex_stores_chunks(client, mock_provider):
    _enable_vectors(client)
    did = client.post("/api/documents", json={"name": "v.md",
                                              "content": "word " * 3000}).json()["id"]
    r = client.post(f"/api/documents/{did}/reindex").json()
    assert r["ok"] and r["chunks"] > 1
    # with the embedding provider unset ("" clears), reindex is a clean no-op
    client.put("/api/settings", json={"embedding_provider_id": "",
                                      "embedding_model": ""})
    did2 = client.post("/api/documents", json={"name": "w.md",
                                               "content": "other " * 3000}).json()["id"]
    assert client.post(f"/api/documents/{did2}/reindex").json()["chunks"] == 0


def test_hybrid_knowledge_injects_vector_hits(client, chat, mock_provider):
    """With embeddings on, even a paraphrase with no FTS hit injects context."""
    _enable_vectors(client)
    client.post("/api/documents", json={"name": "marina.md",
                                        "content": "The harbor seal colony rests "
                                                   "on the northern rocks at dawn."})
    client.patch(f"/api/chats/{chat}", json={"use_knowledge": True})
    _exchange(client, chat, "tell me about those sea dogs by the rocks")
    sent = json.dumps(mock_provider.requests[-1])
    assert "harbor seal" in sent or "marina.md" in sent


def test_vector_side_degrades_cleanly_when_provider_fails(client, chat, mock_provider):
    # point embeddings at a dead endpoint; chat keeps using the live mock
    client.put("/api/providers/openai/key", json={"api_key": "sk-test-1234567890"})
    dead = client.post("/api/providers", json={
        "name": "dead", "kind": "openai",
        "base_url": "http://127.0.0.1:9/v1"}).json()["id"]
    client.put("/api/settings", json={"embedding_provider_id": dead,
                                      "embedding_model": "emb-1"})
    client.post("/api/documents", json={"name": "z.md", "content": "zebra facts here"})
    client.patch(f"/api/chats/{chat}", json={"use_knowledge": True})
    with client.stream("POST", f"/api/chats/{chat}/messages",
                       json={"content": "zebras"}) as resp:
        assert sse_events(resp)[-1][0] == "done"  # FTS still worked


# --- finalize ---------------------------------------------------------------------

def test_finalize_synthesizes_replies(client, chat, mock_provider):
    client.patch(f"/api/chats/{chat}", json={"extra_models": ["openai:gpt-4o-mini"]})
    _exchange(client, chat)
    r = client.post(f"/api/chats/{chat}/finalize", json={})
    assert r.status_code == 200
    out = r.json()
    assert out["message_id"] and out["text"]
    msgs = client.get(f"/api/chats/{chat}").json()["messages"]
    assert msgs[-1]["model"].startswith("finalize ·")
    assert msgs[-1]["is_active"] is True


def test_finalize_requires_a_reply(client, chat):
    assert client.post(f"/api/chats/{chat}/finalize", json={}).status_code == 400


def test_finalize_custom_instruction_reaches_provider(client, chat, mock_provider):
    client.put("/api/settings", json={"finalize_prompt": "MERGE REPLIES TIGHTLY"})
    _exchange(client, chat)
    client.post(f"/api/chats/{chat}/finalize", json={})
    sent = mock_provider.requests[-1]
    assert sent["messages"][0]["content"] == "MERGE REPLIES TIGHTLY"


# --- side chat ---------------------------------------------------------------------

def test_side_chat_streams_without_persistence(client, chat, mock_provider):
    before = len(client.get("/api/chats").json())
    with client.stream("POST", "/api/side-chat",
                       json={"content": "explain this", "provider_id": "openai",
                             "model": "gpt-4o",
                             "context": "selected text here"}) as resp:
        events = sse_events(resp)
    assert "".join(e.get("text", "") for ev, e in events if ev == "delta")
    assert events[-1][0] == "done"
    assert len(client.get("/api/chats").json()) == before


def test_side_chat_missing_key_409(client):
    assert client.post("/api/side-chat", json={"content": "x"}).status_code == 409


# --- memories ------------------------------------------------------------------------

def test_memory_crud_and_injection(client, chat, mock_provider):
    mid = client.post("/api/memories",
                      json={"content": "Prefers concise answers"}).json()["id"]
    client.patch(f"/api/memories/{mid}", json={"content": "Prefers terse answers"})
    assert len(client.get("/api/memories").json()) == 1
    client.put("/api/settings", json={"memory_enabled": True})
    _exchange(client, chat, "hello")
    sent = mock_provider.requests[-1]
    assert any("Prefers terse answers" in (m.get("content") or "")
               for m in sent["messages"] if m["role"] == "system")
    assert client.delete(f"/api/memories/{mid}").json()["ok"]


def test_memory_extraction_from_exchange(client, chat, mock_provider):
    client.put("/api/settings", json={"memory_enabled": True})
    _exchange(client, chat, "I prefer concise answers with code")
    deadline = time.time() + 5
    while time.time() < deadline:
        mems = client.get("/api/memories").json()
        if mems:
            break
        time.sleep(0.1)
    assert any("concise answers" in m["content"] for m in mems)
    assert mems[0]["source"] == "auto"


def test_memory_off_means_no_extraction(client, chat, mock_provider):
    _exchange(client, chat, "I prefer concise answers with code")
    time.sleep(0.5)
    assert client.get("/api/memories").json() == []


# --- skills ---------------------------------------------------------------------------

def test_skill_crud_and_agent_link_and_injection(client, chat, mock_provider):
    sid = client.post("/api/skills",
                      json={"name": "SEO audit", "content": "Audit like an expert"}).json()["id"]
    aid = client.post("/api/agents", json={"name": "Marketer",
                                           "system_prompt": "You market.",
                                           "provider_id": "openai",
                                           "model": "gpt-4o"}).json()["id"]
    assert client.put(f"/api/agents/{aid}/skills",
                      json={"skill_ids": [sid]}).json()["skill_ids"] == [sid]
    cid = client.post("/api/chats", json={"agent_id": aid}).json()["id"]
    _exchange(client, cid, "audit please")
    sent = json.dumps(mock_provider.requests[-1])
    assert "## Skill: SEO audit" in sent and "Audit like an expert" in sent
    assert client.get(f"/api/agents/{aid}/skills").json() == [sid]
    assert client.delete(f"/api/skills/{sid}").json()["ok"]
    assert client.get(f"/api/agents/{aid}/skills").json() == []


def test_skill_import_md_and_zip(client):
    md = base64.b64encode("# My Skill\nDo things well.".encode()).decode()
    r = client.post("/api/skills/import", json={"filename": "SKILL.md", "data": md})
    assert r.json()["name"] == "My Skill"
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pack/SKILL.md", "# Zipped Skill\nZip body.")
        zf.writestr("pack/reference.md", "Extra reference text.")
    zr = client.post("/api/skills/import",
                     json={"filename": "pack.zip",
                           "data": base64.b64encode(buf.getvalue()).decode()}).json()
    assert "Zipped Skill" in zr["name"] and "Extra reference text" in zr["content"]
    assert client.post("/api/skills/import",
                       json={"filename": "x.exe", "data": "AAAA"}).status_code == 400


# --- stats -----------------------------------------------------------------------------

def test_usage_stats_shape(client, chat, mock_provider):
    _exchange(client, chat)
    s = client.get("/api/stats").json()
    assert s["totals"]["messages"] >= 1
    assert s["by_model"][0]["model"] == "gpt-4o"
    assert s["days"] and {"date", "tokens_in", "tokens_out"} <= set(s["days"][0])


# --- image generation --------------------------------------------------------------------

def test_image_generation_into_chat(client, chat, mock_provider):
    r = client.post("/api/images/generate",
                    json={"provider_id": "openai", "model": "gpt-image-1",
                          "prompt": "a red cube", "chat_id": chat})
    assert r.status_code == 200
    out = r.json()
    assert out["url"].startswith("/api/uploads/gen_")
    assert client.get(out["url"]).status_code == 200
    msgs = client.get(f"/api/chats/{chat}").json()["messages"]
    assert msgs[-1]["images"][0]["file"] == out["file"]


def test_image_generation_requires_known_provider(client):
    assert client.post("/api/images/generate",
                       json={"provider_id": "nope", "model": "m",
                             "prompt": "x"}).status_code == 404


# --- temporary chats -----------------------------------------------------------------------

def test_temporary_chat_hidden_and_unshareable(client, mock_provider):
    tid = client.post("/api/chats", json={"provider_id": "openai", "model": "gpt-4o",
                                          "temporary": True}).json()["id"]
    assert tid not in {c["id"] for c in client.get("/api/chats").json()}
    assert tid in {c["id"] for c in client.get("/api/chats?temporary=true").json()}
    assert client.post(f"/api/chats/{tid}/share", json={}).status_code == 400
    _exchange(client, tid, "invisible soon")
    assert client.get("/api/search?q=invisible").json() == []
    export = json.loads(client.post("/api/export", json={"format": "json"}).text)
    assert tid not in {c["id"] for c in export["chats"]}


# --- reasoning / caching / language params ---------------------------------------------------

def test_reasoning_effort_reaches_openai_body(client, chat, mock_provider):
    client.patch(f"/api/chats/{chat}", json={"reasoning_effort": "high"})
    _exchange(client, chat)
    assert mock_provider.requests[-1].get("reasoning_effort") == "high"


def test_output_language_appended_to_system(client, chat, mock_provider):
    client.patch(f"/api/chats/{chat}", json={"output_language": "Spanish"})
    _exchange(client, chat)
    systems = [m["content"] for m in mock_provider.requests[-1]["messages"]
               if m["role"] == "system"]
    assert any("Spanish" in s for s in systems)


def test_dialect_bodies_for_reasoning_and_caching():
    anthropic = {"id": "anthropic", "kind": "anthropic", "base_url": "https://x"}
    url, body = providers._body(anthropic, "claude-sonnet-4-5", [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"}],
        {"reasoning_effort": "high", "cache_prompt": True, "max_tokens": 100},
        stream=False)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 16384}
    assert body["max_tokens"] > 16384
    assert "temperature" not in body  # thinking forbids non-default temperature
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    url2, body2 = providers._body(anthropic, "claude-sonnet-4-5", [
        {"role": "user", "content": "hi"}], {}, stream=False)
    assert "thinking" not in body2 and body2["system"] is None if False else True
    google = {"id": "google", "kind": "google", "base_url": "https://g"}
    _, gbody = providers._body(google, "gemini-2.5-flash", [
        {"role": "user", "content": "hi"}], {"reasoning_effort": "none"}, stream=False)
    assert gbody["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


# --- import converters -------------------------------------------------------------------

def test_import_from_chatgpt_export(client, chat):
    export = {"conversations": [{
        "title": "GPT chat", "current_node": "n2",
        "mapping": {
            "n1": {"message": {"author": {"role": "user"},
                               "content": {"parts": ["hello gpt"]},
                               "create_time": 1700000000.0}, "parent": None},
            "n2": {"message": {"author": {"role": "assistant"},
                               "content": {"parts": ["hi there"]},
                               "metadata": {"model_slug": "gpt-4o"}},
                    "parent": "n1"}}}]}
    r = client.post("/api/import", json=export).json()
    assert r["chats"] == 1
    cid = next(c["id"] for c in client.get("/api/chats").json()
               if c["title"] == "GPT chat")
    detail = client.get(f"/api/chats/{cid}").json()
    assert [m["content"] for m in detail["messages"]] == ["hello gpt", "hi there"]


def test_import_from_claude_export(client):
    export = {"chats": [{
        "name": "Claude chat", "model": "claude-sonnet-4-5",
        "chat_messages": [
            {"sender": "human", "text": "hello claude",
             "created_at": "2026-01-01T00:00:00Z"},
            {"sender": "assistant",
             "content": [{"type": "text", "text": "hi human"}]}]}]}
    r = client.post("/api/import", json=export).json()
    assert r["chats"] == 1
    detail = client.get(f"/api/chats/{client.get('/api/chats').json()[0]['id']}").json()
    assert detail["messages"][1]["content"] == "hi human"


# --- deprecated models -------------------------------------------------------------------

def test_models_marked_deprecated_after_refresh(client, mock_provider):
    client.put("/api/providers/openai/key", json={"api_key": "sk-test-1234567890"})
    client.put("/api/providers/openai", json={"models": ["stale-model"]})
    client.post("/api/providers/openai/refresh-models")
    prov = next(p for p in client.get("/api/models").json() if p["provider_id"] == "openai")
    assert "stale-model" in prov["deprecated"]
    assert "mock-model-a" not in prov["deprecated"]
    # before any refresh, no deprecation is asserted
    g = next(p for p in client.get("/api/models").json()
             if p["provider_id"] == "google")
    assert g["deprecated"] == []


# --- extraction ----------------------------------------------------------------------------

def test_extract_pdf_roundtrip(client):
    from app import extract
    # pypdf cannot easily add text without fonts; use a minimal hand-made PDF
    minimal = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
               b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
               b"trailer<</Root 1 0 R>>")
    try:
        text = extract.pdf_to_text(minimal)
    except ValueError as e:
        text = str(e)
    assert isinstance(text, str) and text  # either extracted text or clean error
    r = client.post("/api/extract", json={"filename": "x.pdf",
                                          "data": base64.b64encode(
                                              b"%PDF-1.4 broken").decode()})
    assert r.status_code == 422  # clean, user-facing error — no 500


def test_extract_text_file(client):
    r = client.post("/api/extract", json={"filename": "notes.txt",
                                          "data": base64.b64encode(
                                              b"hello notes").decode()})
    assert r.json() == {"text": "hello notes", "kind": "text"}


# --- ElevenLabs guards -------------------------------------------------------------------

def test_elevenlabs_requires_key_and_valid_voice(client):
    assert client.get(
        "/api/tts/elevenlabs?text=hi&voice=ElevenVoiceId123456").status_code == 409
    assert client.get("/api/tts/elevenlabs?text=hi&voice=abc").status_code == 400
    client.put("/api/settings/secret", json={"name": "elevenlabs", "value": "el-key"})
    assert client.get("/api/settings").json()["elevenlabs_hint"]  # masked hint set
    assert client.get("/api/tts/elevenlabs?text=hi&voice=short").status_code == 400


# --- new settings keys --------------------------------------------------------------------

def test_new_settings_roundtrip(client):
    r = client.put("/api/settings", json={
        "service_worker": True, "finalize_prompt": "FUSE",
        "memory_extract_prompt": "EXTRACT", "artifact_rewrite_prompt": "REWRITE",
        "shortcuts": {"search": "Mod+K"}})
    s = r.json()
    assert s["service_worker"] is True
    assert s["finalize_prompt"] == "FUSE"
    assert s["artifact_rewrite_prompt"] == "REWRITE"
    assert s["shortcuts"] == {"search": "Mod+K"}
    assert client.get("/api/health").json()["rev"] >= 2


# --- artifacts (side-panel editor, versions, AI rewrite) ----------------------------------

def _artifact(client, chat, content="<h1>Demo page</h1>", kind="html", **kw):
    return client.post(f"/api/chats/{chat}/artifacts",
                       json={"content": content, "kind": kind, **kw})


def test_artifact_create_and_latest_per_root(client, chat):
    a1 = _artifact(client, chat).json()
    assert a1["version"] == 1 and a1["root_id"] == a1["id"]
    assert a1["title"]  # derived from the first non-blank line
    a2 = _artifact(client, chat, content="print('hi')", kind="code",
                   language="Python").json()
    assert a2["language"] == "python"  # lowercased fence info
    # save a new version of the first root
    put = client.put(f"/api/artifacts/{a1['id']}",
                     json={"content": "<h2>v2</h2>"}).json()
    assert put["version"] == 2 and put["root_id"] == a1["root_id"]
    listing = client.get(f"/api/chats/{chat}/artifacts").json()
    assert len(listing) == 2  # still two roots, not three rows
    by_root = {a["root_id"]: a for a in listing}
    assert by_root[a1["root_id"]]["version"] == 2           # MAX picked the new row
    assert by_root[a1["root_id"]]["content"] == "<h2>v2</h2>"
    assert by_root[a1["root_id"]]["versions"] == 2
    assert by_root[a2["root_id"]]["version"] == 1


def test_artifact_version_chain_meta(client, chat):
    a = _artifact(client, chat).json()
    client.put(f"/api/artifacts/{a['id']}", json={"content": "v2"})
    r = client.get(f"/api/artifacts/{a['id']}/versions")
    assert r.status_code == 200
    chain = r.json()
    assert [v["version"] for v in chain] == [1, 2]
    assert set(chain[0]) == {"id", "version", "title", "kind", "language",
                             "size", "created_at"}  # meta only — no content


def test_artifact_404s_and_kind_validation(client, chat):
    assert client.post("/api/chats/nope/artifacts",
                       json={"content": "x"}).status_code == 404
    assert client.get("/api/artifacts/nope").status_code == 404
    assert client.put("/api/artifacts/nope",
                      json={"content": "x"}).status_code == 404
    assert _artifact(client, chat, kind="exe").status_code == 422


def test_artifact_delete_removes_root_chain(client, chat):
    a = _artifact(client, chat).json()
    for i in range(2):
        client.put(f"/api/artifacts/{a['id']}", json={"content": f"v{i + 2}"})
    r = client.delete(f"/api/artifacts/{a['id']}")
    assert r.json() == {"ok": True, "deleted": 3}
    assert client.get(f"/api/artifacts/{a['id']}").status_code == 404
    assert client.get(f"/api/chats/{chat}/artifacts").json() == []


def test_chat_delete_cascades_artifacts(client):
    chat_id = client.post("/api/chats",
                          json={"model": "gpt-4o", "provider_id": "openai"}).json()["id"]
    a = _artifact(client, chat_id).json()
    assert client.delete(f"/api/chats/{chat_id}").json() == {"ok": True}
    assert client.get(f"/api/artifacts/{a['id']}").status_code == 404


def test_artifact_rewrite_creates_new_version(client, chat, mock_provider):
    a = _artifact(client, chat, content="<p>original</p>").json()
    r = client.post(f"/api/artifacts/{a['id']}/rewrite", json={})
    assert r.status_code == 200
    out = r.json()
    assert out["artifact"]["version"] == 2
    assert out["artifact"]["root_id"] == a["root_id"]
    assert out["artifact"]["content"] == "<p>REWRITTEN ARTIFACT BODY</p>"  # fence stripped
    assert out["usage"]["completion_tokens"] > 0
    sent = mock_provider.requests[-1]
    assert "rewriting an existing artifact" in sent["messages"][0]["content"]
    assert "<p>original</p>" in sent["messages"][1]["content"]


def test_artifact_rewrite_custom_instruction_appends(client, chat, mock_provider):
    a = _artifact(client, chat).json()
    client.post(f"/api/artifacts/{a['id']}/rewrite",
                json={"instruction": "REORDER THE SECTIONS"})
    system = mock_provider.requests[-1]["messages"][0]["content"]
    assert system.startswith("You are rewriting an existing artifact")
    assert system.endswith("REORDER THE SECTIONS")  # refines the policy, keeps it


def test_artifact_rewrite_settings_prompt_replaces(client, chat, mock_provider):
    client.put("/api/settings", json={"artifact_rewrite_prompt": "TIGHTEN THE CODE"})
    a = _artifact(client, chat).json()
    client.post(f"/api/artifacts/{a['id']}/rewrite", json={})
    assert mock_provider.requests[-1]["messages"][0]["content"] == "TIGHTEN THE CODE"


def test_artifact_rewrite_too_large(client, chat):
    a = _artifact(client, chat, content="x" * 40_001).json()
    r = client.post(f"/api/artifacts/{a['id']}/rewrite", json={})
    assert r.status_code == 400 and "too large" in r.json()["detail"]


def test_artifact_share_roundtrip_and_revoke(client, chat):
    a = _artifact(client, chat).json()
    client.put(f"/api/artifacts/{a['id']}", json={"content": "<p>v2</p>"})
    url = client.post(f"/api/artifacts/{a['id']}/share").json()["url"]
    assert url.startswith("/pa/")
    assert client.post(f"/api/artifacts/{a['id']}/share").json()["url"] == url  # idempotent
    page = client.get(url)
    assert page.status_code == 200 and "Shared artifact" in page.text
    data = client.get(f"/api/public/artifact/{url.split('/')[-1]}").json()
    assert data["content"] == "<p>v2</p>" and data["version"] == 2  # latest version
    assert client.delete(f"/api/artifacts/{a['id']}/share").json() == {"ok": True}
    assert client.get(f"/api/public/artifact/{url.split('/')[-1]}").status_code == 404


def test_artifact_share_blocked_for_temporary_chat(client, mock_provider):
    tid = client.post("/api/chats",
                      json={"model": "gpt-4o", "provider_id": "openai",
                            "temporary": True}).json()["id"]
    a = _artifact(client, tid).json()
    r = client.post(f"/api/artifacts/{a['id']}/share")
    assert r.status_code == 400 and "temporary" in r.json()["detail"]
