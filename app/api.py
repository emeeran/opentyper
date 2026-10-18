"""All /api routes."""

import asyncio
import base64
import binascii
import json
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from . import crypto, db, providers, tts, vectors

router = APIRouter(prefix="/api")

REV = 6  # bumped per release; the service worker keys its cache on this

_bg_tasks: set[asyncio.Task] = set()

IMAGE_MIMES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
               "image/gif": "gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ARTIFACT_CHARS = 200_000        # artifact content cap (create + save)
MAX_ARTIFACT_REWRITE_CHARS = 40_000  # content cap fed to the model for a rewrite


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def rowdict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def get_setting(con: sqlite3.Connection, key: str, default: str | None = None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def load_provider(con: sqlite3.Connection, provider_id: str) -> dict:
    row = con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"provider '{provider_id}' not found")
    p = dict(row)
    p["models"] = json.loads(p["models"] or "[]")
    return p


def api_key_for(con: sqlite3.Connection, provider_id: str) -> str:
    row = con.execute("SELECT api_key_encrypted FROM providers WHERE id=?",
                      (provider_id,)).fetchone()
    if not row or not row["api_key_encrypted"]:
        raise HTTPException(409, f"no API key set for provider '{provider_id}'")
    return crypto.decrypt_secret(row["api_key_encrypted"])


def estimate_tokens(text: str) -> int:
    """Rough chars/4 fallback when the provider doesn't report usage."""
    return max(1, len(text or "") // 4)


def price_for(con: sqlite3.Connection, model: str) -> tuple[float, float] | None:
    """(usd_per_mtok_in, usd_per_mtok_out) from settings.model_prices, a JSON
    object of {"model-prefix": {"in": x, "out": y}}. First prefix match wins."""
    try:
        prices = json.loads(get_setting(con, "model_prices", "{}") or "{}")
    except json.JSONDecodeError:
        return None
    model = (model or "").lower()
    for prefix, p in prices.items():
        if model.startswith(prefix.lower()) and isinstance(p, dict) \
                and "in" in p and "out" in p:
            return (float(p["in"]), float(p["out"]))
    return None


def cost_of(price: tuple[float, float], prompt_tokens: int, completion_tokens: int) -> float:
    return round((prompt_tokens * price[0] + completion_tokens * price[1]) / 1e6, 6)


def save_image(chat_id: str, data_url: str) -> dict:
    """Validate a data-URL image, store it under data/uploads/, return its ref."""
    m = re.fullmatch(r"data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=\s]+)",
                     data_url or "")
    if not m:
        raise HTTPException(400, "images must be data URLs (png/jpeg/webp/gif)")
    mime, b64 = m.group(1), m.group(2)
    try:
        raw = base64.b64decode(b64, validate=False)
    except (ValueError, binascii.Error) as e:
        raise HTTPException(400, "invalid image data") from e
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "image too large (max 8 MB)")
    name = f"{chat_id}_{new_id()}.{IMAGE_MIMES[mime]}"
    path = Path(db.DATA_DIR) / "uploads" / name
    path.write_bytes(raw)
    return {"file": name, "mime": mime}


def knowledge_snippets(con: sqlite3.Connection, query: str, k: int = 4) -> list[str]:
    """FTS keyword snippets from the documents library (keyword half of the
    hybrid retrieval — the vector half runs async in hybrid_knowledge)."""
    words = [w for w in re.findall(r"\w{3,}", query or "")][:12]
    if not words:
        return []
    match = " OR ".join(f'"{w}"' for w in words)
    try:
        rows = con.execute(
            "SELECT d.name, snippet(documents_fts, 0, '', '', '…', 24) AS snip "
            "FROM documents_fts JOIN documents d ON d.rowid = documents_fts.rowid "
            "WHERE documents_fts MATCH ? ORDER BY rank LIMIT ?", (match, k)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [f"[{r['name']}] {r['snip']}" for r in rows]


async def hybrid_knowledge(con: sqlite3.Connection, query: str, k: int = 4) -> list[str]:
    """FTS snippets merged with embedding-similarity chunks when a vector
    provider is configured. Embedding failures degrade to FTS-only."""
    out = knowledge_snippets(con, query)
    provider_id, model = vectors.settings_keys(con)
    if provider_id and model and query.strip():
        try:
            provider = load_provider(con, provider_id)
            api_key = api_key_for(con, provider_id)
            vecs = await vectors.embed_texts(provider, api_key, model, [query])
            for snippet in vectors.retrieve(con, vecs[0], k=k):
                if snippet not in out:
                    out.append(snippet)
        except Exception:
            pass  # vector side is best-effort; FTS result already in hand
    return out[:k + 1]


# --- health / auth -----------------------------------------------------------

@router.get("/health")
def health():
    return {"ok": True, "rev": REV}


class LoginIn(BaseModel):
    password: str


@router.post("/auth/login")
def login(body: LoginIn, response: Response):
    con = db.connect()
    try:
        stored = get_setting(con, "app_password_hash")
    finally:
        con.close()
    if not stored or not crypto.verify_password(body.password, stored):
        raise HTTPException(401, "invalid password")
    response.set_cookie(crypto.SESSION_COOKIE, crypto.make_session_token(stored),
                        httponly=True, samesite="lax", max_age=crypto.SESSION_TTL,
                        path="/")
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(crypto.SESSION_COOKIE, path="/")
    return {"ok": True}


# --- models ------------------------------------------------------------------

@router.get("/models")
def list_models():
    con = db.connect()
    try:
        rows = con.execute("SELECT * FROM providers ORDER BY is_builtin DESC, name").fetchall()
        out = []
        for r in rows:
            p = dict(r)
            custom = json.loads(p["models"] or "[]")
            if p["is_builtin"]:
                models = providers.BUILTIN[p["id"]]["models"] + [
                    m for m in custom if m not in providers.BUILTIN[p["id"]]["models"]]
            else:
                models = custom
            remote = json.loads(p.get("remote_models") or "[]")
            out.append({"provider_id": p["id"], "name": p["name"], "kind": p["kind"],
                        "has_key": bool(p["api_key_encrypted"]), "models": models,
                        # after a live refresh, stored ids missing from the
                        # provider's current catalog are flagged
                        "remote_count": len(remote),
                        "deprecated": ([m for m in models if m not in remote]
                                       if remote else [])})
        return out
    finally:
        con.close()


# --- providers ---------------------------------------------------------------

class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(pattern="^(openai|anthropic|google)$")
    base_url: str | None = Field(None, max_length=500)


@router.post("/providers")
def create_provider(body: ProviderCreate):
    """Custom provider on an OpenAI-compatible (or Anthropic/Google) endpoint."""
    con = db.connect()
    try:
        pid = new_id()
        ts = now()
        con.execute("INSERT INTO providers (id, name, kind, base_url, models, "
                    "is_builtin, created_at, updated_at) VALUES (?,?,?,?,'[]',0,?,?)",
                    (pid, body.name, body.kind, body.base_url, ts, ts))
        con.commit()
        return rowdict(con.execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone())
    finally:
        con.close()


class ProviderPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    base_url: str | None = Field(None, max_length=500)
    models: list[str] | None = None


@router.get("/providers")
def list_providers():
    con = db.connect()
    try:
        rows = con.execute("SELECT * FROM providers ORDER BY is_builtin DESC, name").fetchall()
        return [{**dict(r), "models": json.loads(r["models"] or "[]"),
                 "api_key_encrypted": None} for r in rows]
    finally:
        con.close()


@router.put("/providers/{provider_id}")
def update_provider(provider_id: str, body: ProviderPatch):
    con = db.connect()
    try:
        row = con.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
        if not row:
            raise HTTPException(404, "provider not found")
        if body.name is not None:
            con.execute("UPDATE providers SET name=?, updated_at=? WHERE id=?",
                        (body.name, now(), provider_id))
        if body.base_url is not None:
            url = body.base_url.strip() or None
            con.execute("UPDATE providers SET base_url=?, updated_at=? WHERE id=?",
                        (url, now(), provider_id))
        if body.models is not None:
            con.execute("UPDATE providers SET models=?, updated_at=? WHERE id=?",
                        (json.dumps(body.models), now(), provider_id))
        con.commit()
        return rowdict(con.execute("SELECT * FROM providers WHERE id=?",
                                   (provider_id,)).fetchone())
    finally:
        con.close()


@router.post("/providers/{provider_id}/refresh-models")
async def refresh_provider_models(provider_id: str):
    """Fetch the provider's live model list and merge it into the stored list."""
    con = db.connect()
    try:
        provider = load_provider(con, provider_id)
        api_key = api_key_for(con, provider_id)
    finally:
        con.close()
    async with httpx.AsyncClient() as client:
        fetched = await providers.list_remote_models(provider, api_key, client)
    merged = list(dict.fromkeys(provider["models"] + fetched))  # already parsed
    con = db.connect()
    try:
        con.execute("UPDATE providers SET models=?, remote_models=?, updated_at=? "
                    "WHERE id=?",
                    (json.dumps(merged), json.dumps(fetched), now(), provider_id))
        con.commit()
    finally:
        con.close()
    return {"ok": True, "fetched": len(fetched), "models": merged}


class TestIn(BaseModel):
    model: str | None = Field(None, max_length=200)


@router.post("/providers/{provider_id}/test")
async def test_provider_endpoint(provider_id: str, body: TestIn | None = None):
    """Send a tiny completion to prove the key/endpoint/model generate a reply."""
    con = db.connect()
    try:
        provider = load_provider(con, provider_id)
        api_key = api_key_for(con, provider_id)
    finally:
        con.close()
    stored = provider["models"]  # already parsed to a list by load_provider
    model = (body.model if body and body.model else None) \
        or (stored[0] if stored else None) \
        or (providers.BUILTIN[provider_id]["models"][0]
            if provider_id in providers.BUILTIN else None)
    if not model:
        raise HTTPException(400, "no model available to test — fetch or set models first")
    started = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            reply = await providers.test_provider(provider, api_key, model, client)
        return {"ok": True, "model": model,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "reply": reply.strip()[:120]}
    except (providers.ProviderError, httpx.HTTPError) as e:
        return {"ok": False, "model": model,
                "error": providers._scrub(str(e) or e.__class__.__name__, api_key)}


class KeyIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=500)


@router.put("/providers/{provider_id}/key")
def set_provider_key(provider_id: str, body: KeyIn):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM providers WHERE id=?", (provider_id,)).fetchone():
            raise HTTPException(404, "provider not found")
        con.execute("UPDATE providers SET api_key_encrypted=?, key_hint=?, updated_at=? "
                    "WHERE id=?", (crypto.encrypt_secret(body.api_key),
                                   crypto.key_hint(body.api_key), now(), provider_id))
        con.commit()
        return {"ok": True, "key_hint": crypto.key_hint(body.api_key)}
    finally:
        con.close()


@router.delete("/providers/{provider_id}/key")
def delete_provider_key(provider_id: str):
    con = db.connect()
    try:
        con.execute("UPDATE providers SET api_key_encrypted=NULL, key_hint=NULL, "
                    "updated_at=? WHERE id=?", (now(), provider_id))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


# --- folders -----------------------------------------------------------------

class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider_id: str | None = None
    model: str | None = None
    agent_id: str | None = None
    system_prompt: str | None = None


@router.get("/folders")
def list_folders():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM folders ORDER BY position, name").fetchall()]
    finally:
        con.close()


@router.post("/folders")
def create_folder(body: FolderIn):
    con = db.connect()
    try:
        fid = new_id()
        ts = now()
        con.execute("INSERT INTO folders (id, name, provider_id, model, agent_id, "
                    "system_prompt, position, created_at, updated_at) VALUES (?,?,?,?,?,"
                    "?,(SELECT COALESCE(MAX(position),0)+1 FROM folders),?,?)",
                    (fid, body.name, body.provider_id, body.model, body.agent_id,
                     body.system_prompt, ts, ts))
        con.commit()
        return rowdict(con.execute("SELECT * FROM folders WHERE id=?", (fid,)).fetchone())
    finally:
        con.close()


@router.patch("/folders/{folder_id}")
def update_folder(folder_id: str, body: FolderIn):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone():
            raise HTTPException(404, "folder not found")
        con.execute("UPDATE folders SET name=?, provider_id=?, model=?, agent_id=?, "
                    "system_prompt=?, updated_at=? WHERE id=?",
                    (body.name, body.provider_id, body.model, body.agent_id,
                     body.system_prompt, now(), folder_id))
        con.commit()
        return rowdict(con.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone())
    finally:
        con.close()


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: str):
    con = db.connect()
    try:
        con.execute("DELETE FROM folders WHERE id=?", (folder_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


# --- prompts -----------------------------------------------------------------

class PromptIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=1000)
    content: str = Field(min_length=1, max_length=100_000)
    tags: list[str] = []


@router.get("/prompts")
def list_prompts():
    con = db.connect()
    try:
        return [{**dict(r), "tags": json.loads(r["tags"])} for r in
                con.execute("SELECT * FROM prompts ORDER BY title").fetchall()]
    finally:
        con.close()


@router.post("/prompts")
def create_prompt(body: PromptIn):
    con = db.connect()
    try:
        pid = new_id()
        ts = now()
        con.execute("INSERT INTO prompts (id, title, description, content, tags, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                    (pid, body.title, body.description, body.content,
                     json.dumps(body.tags), ts, ts))
        con.commit()
        row = con.execute("SELECT * FROM prompts WHERE id=?", (pid,)).fetchone()
        return {**dict(row), "tags": json.loads(row["tags"])}
    finally:
        con.close()


@router.patch("/prompts/{prompt_id}")
def update_prompt(prompt_id: str, body: PromptIn):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM prompts WHERE id=?", (prompt_id,)).fetchone():
            raise HTTPException(404, "prompt not found")
        con.execute("UPDATE prompts SET title=?, description=?, content=?, tags=?, "
                    "updated_at=? WHERE id=?",
                    (body.title, body.description, body.content,
                     json.dumps(body.tags), now(), prompt_id))
        con.commit()
        row = con.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
        return {**dict(row), "tags": json.loads(row["tags"])}
    finally:
        con.close()


@router.delete("/prompts/{prompt_id}")
def delete_prompt(prompt_id: str):
    con = db.connect()
    try:
        con.execute("DELETE FROM prompts WHERE id=?", (prompt_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


# --- agents ------------------------------------------------------------------

class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=1000)
    system_prompt: str = Field(min_length=1, max_length=100_000)
    model: str | None = None
    provider_id: str | None = None
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, gt=0, le=200_000)

    @field_validator("provider_id")
    @classmethod
    def provider_exists(cls, v):
        if v is not None:
            con = db.connect()
            try:
                if not con.execute("SELECT 1 FROM providers WHERE id=?", (v,)).fetchone():
                    raise ValueError("provider not found")
            finally:
                con.close()
        return v


@router.get("/agents")
def list_agents():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM agents ORDER BY name").fetchall()]
    finally:
        con.close()


@router.post("/agents")
def create_agent(body: AgentIn):
    con = db.connect()
    try:
        aid = new_id()
        ts = now()
        con.execute("INSERT INTO agents (id, name, description, system_prompt, model, "
                    "provider_id, temperature, max_tokens, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (aid, body.name, body.description, body.system_prompt, body.model,
                     body.provider_id, body.temperature, body.max_tokens, ts, ts))
        con.commit()
        return rowdict(con.execute("SELECT * FROM agents WHERE id=?", (aid,)).fetchone())
    finally:
        con.close()


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentIn):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
            raise HTTPException(404, "agent not found")
        con.execute("UPDATE agents SET name=?, description=?, system_prompt=?, model=?, "
                    "provider_id=?, temperature=?, max_tokens=?, updated_at=? WHERE id=?",
                    (body.name, body.description, body.system_prompt, body.model,
                     body.provider_id, body.temperature, body.max_tokens, now(), agent_id))
        con.commit()
        return rowdict(con.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone())
    finally:
        con.close()


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str):
    con = db.connect()
    try:
        con.execute("DELETE FROM agents WHERE id=?", (agent_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


# --- chats -------------------------------------------------------------------

class ChatCreate(BaseModel):
    model: str | None = Field(None, min_length=1, max_length=200)
    provider_id: str | None = None
    title: str | None = Field(None, max_length=300)
    folder_id: str | None = None
    agent_id: str | None = None
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, gt=0, le=200_000)
    top_p: float | None = Field(None, gt=0, le=1)
    system_prompt: str | None = None
    context_limit: int | None = Field(None, ge=0, le=1000)
    frequency_penalty: float | None = Field(None, ge=-2, le=2)
    presence_penalty: float | None = Field(None, ge=-2, le=2)
    stream: bool | None = None
    extra_models: list[str] = []
    use_knowledge: bool | None = None
    reasoning_effort: str | None = Field(None, pattern="^(none|low|medium|high|xhigh|max)$")
    output_language: str | None = Field(None, max_length=60)
    cache_prompt: bool | None = None
    temporary: bool | None = None


class ChatPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    folder_id: str | None = None
    model: str | None = Field(None, min_length=1, max_length=200)
    provider_id: str | None = None
    agent_id: str | None = None
    temperature: float | None = Field(None, ge=0, le=2)
    max_tokens: int | None = Field(None, gt=0, le=200_000)
    top_p: float | None = Field(None, gt=0, le=1)
    system_prompt: str | None = None
    pinned: bool | None = None
    tags: list[str] | None = None
    archived: bool | None = None
    context_limit: int | None = Field(None, ge=0, le=1000)
    frequency_penalty: float | None = Field(None, ge=-2, le=2)
    presence_penalty: float | None = Field(None, ge=-2, le=2)
    stream: bool | None = None
    extra_models: list[str] | None = None
    use_knowledge: bool | None = None
    reasoning_effort: str | None = Field(None, pattern="^(none|low|medium|high|xhigh|max)$")
    output_language: str | None = Field(None, max_length=60)
    cache_prompt: bool | None = None


def chat_dict(con: sqlite3.Connection, row: sqlite3.Row) -> dict:
    d = dict(row)
    d["pinned"] = bool(d["pinned"])
    d["archived"] = bool(d["archived"])
    d["stream"] = bool(d["stream"])
    d["use_knowledge"] = bool(d["use_knowledge"])
    d["cache_prompt"] = bool(d["cache_prompt"])
    d["temporary"] = bool(d["temporary"])
    d["tags"] = json.loads(d["tags"] or "[]")
    d["extra_models"] = json.loads(d["extra_models"] or "[]")
    d["message_count"] = con.execute(
        "SELECT COUNT(*) FROM messages WHERE chat_id=? AND is_active=1",
        (d["id"],)).fetchone()[0]
    return d


@router.get("/chats")
def list_chats(archived: bool = False, temporary: bool = False):
    con = db.connect()
    try:
        rows = con.execute("SELECT * FROM chats WHERE archived=? AND temporary=? "
                           "ORDER BY pinned DESC, updated_at DESC",
                           (1 if archived else 0, 1 if temporary else 0)).fetchall()
        return [chat_dict(con, r) for r in rows]
    finally:
        con.close()


def _apply_agent_defaults(con: sqlite3.Connection, body: dict) -> dict:
    """Copy agent model/params/system prompt onto the chat at creation time."""
    if body.get("agent_id"):
        agent = con.execute("SELECT * FROM agents WHERE id=?",
                            (body["agent_id"],)).fetchone()
        if not agent:
            raise HTTPException(404, "agent not found")
        a = dict(agent)
        body.setdefault("model", a["model"])
        body.setdefault("provider_id", a["provider_id"])
        body.setdefault("temperature", a["temperature"])
        body.setdefault("max_tokens", a["max_tokens"])
        body["system_prompt"] = a["system_prompt"]
    return body


@router.post("/chats")
def create_chat(body: ChatCreate):
    con = db.connect()
    try:
        data = body.model_dump(exclude_none=True)
        _apply_agent_defaults(con, data)
        if data.get("folder_id"):
            if not con.execute("SELECT 1 FROM folders WHERE id=?",
                               (data["folder_id"],)).fetchone():
                raise HTTPException(404, "folder not found")
            f = con.execute("SELECT provider_id, model, agent_id, system_prompt FROM "
                            "folders WHERE id=?", (data["folder_id"],)).fetchone()
            data.setdefault("system_prompt", f["system_prompt"])
            if "agent_id" not in data and f["agent_id"]:
                data["agent_id"] = f["agent_id"]
            data.setdefault("provider_id", f["provider_id"])
            data.setdefault("model", f["model"])
        if not data.get("provider_id"):
            raise HTTPException(400, "provider_id is required")
        if not data.get("model"):
            raise HTTPException(400, "model is required (directly, via folder, or via agent)")
        load_provider(con, data["provider_id"])  # 404 if unknown
        # model-family defaults, lowest priority (body > agent > folder > these)
        for key, value in providers.recommended_params(data["model"]).items():
            data.setdefault(key, value)
        cid = new_id()
        ts = now()
        con.execute(
            "INSERT INTO chats (id, title, folder_id, model, provider_id, agent_id, "
            "temperature, max_tokens, top_p, system_prompt, pinned, tags, archived, "
            "context_limit, frequency_penalty, presence_penalty, stream, extra_models, "
            "use_knowledge, reasoning_effort, output_language, cache_prompt, temporary, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,'0','[]','0',?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, data.get("title") or "New chat", data.get("folder_id"), data["model"],
             data["provider_id"], data.get("agent_id"), data.get("temperature"),
             data.get("max_tokens"), data.get("top_p"), data.get("system_prompt"),
             data.get("context_limit"), data.get("frequency_penalty"),
             data.get("presence_penalty"),
             0 if data.get("stream") is False else 1,
             json.dumps(data.get("extra_models") or []),
             1 if data.get("use_knowledge") else 0,
             data.get("reasoning_effort"), data.get("output_language"),
             1 if data.get("cache_prompt") else 0,
             1 if data.get("temporary") else 0, ts, ts))
        con.commit()
        return chat_dict(con, con.execute("SELECT * FROM chats WHERE id=?",
                                          (cid,)).fetchone())
    finally:
        con.close()


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str):
    con = db.connect()
    try:
        row = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if not row:
            raise HTTPException(404, "chat not found")
        out = chat_dict(con, row)
        out["messages"] = []
        for m in con.execute(
                "SELECT id, role, content, model, interrupted, images, prompt_tokens, "
                "completion_tokens, cost, error, variant_group, is_active, created_at "
                "FROM messages WHERE chat_id=? ORDER BY created_at, rowid",
                (chat_id,)).fetchall():
            d = dict(m)
            d["images"] = json.loads(d["images"] or "[]")
            d["is_active"] = bool(d["is_active"])
            out["messages"].append(d)
        return out
    finally:
        con.close()


@router.patch("/chats/{chat_id}")
def update_chat(chat_id: str, body: ChatPatch):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone():
            raise HTTPException(404, "chat not found")
        fields = {k: v for k, v in body.model_dump(exclude_none=True).items()}
        # empty string means "clear" for nullable references/text
        for k in ("folder_id", "agent_id", "system_prompt"):
            if fields.get(k) == "":
                fields[k] = None
        for k in ("tags", "extra_models"):  # JSON columns
            if k in fields:
                fields[k] = json.dumps(fields[k])
        if fields.get("folder_id"):
            if not con.execute("SELECT 1 FROM folders WHERE id=?",
                               (fields["folder_id"],)).fetchone():
                raise HTTPException(404, "folder not found")
        if fields.get("agent_id"):
            if not con.execute("SELECT 1 FROM agents WHERE id=?",
                               (fields["agent_id"],)).fetchone():
                raise HTTPException(404, "agent not found")
        if "provider_id" in fields and fields["provider_id"]:
            load_provider(con, fields["provider_id"])
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE chats SET {sets}, updated_at=? WHERE id=?",
                        (*fields.values(), now(), chat_id))
            con.commit()
        return chat_dict(con, con.execute("SELECT * FROM chats WHERE id=?",
                                          (chat_id,)).fetchone())
    finally:
        con.close()


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone():
            raise HTTPException(404, "chat not found")
        # artifact rows cascade via FK; share tokens can't (root_id is not a key)
        con.execute("DELETE FROM artifact_shares WHERE root_id IN "
                    "(SELECT root_id FROM artifacts WHERE chat_id=?)", (chat_id,))
        con.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


# --- streaming chat ----------------------------------------------------------

class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    images: list[str] = Field(default=[], max_length=4)  # data URLs
    use_knowledge: bool | None = None  # override chat.use_knowledge for this send


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _load_images(refs: list[dict]) -> list[tuple[str, str]]:
    """Read stored image refs from disk as (mime, base64) for the provider."""
    out = []
    for ref in refs or []:
        try:
            raw = (Path(db.DATA_DIR) / "uploads" / Path(ref["file"]).name).read_bytes()
        except OSError:
            continue
        out.append((ref["mime"], base64.b64encode(raw).decode()))
    return out


def _history(con: sqlite3.Connection, chat_id: str) -> list[dict]:
    rows = con.execute("SELECT role, content, images, error FROM messages "
                       "WHERE chat_id=? AND is_active=1 "
                       "ORDER BY created_at, rowid", (chat_id,)).fetchall()
    # pure-error rows (no captured text) must not leak failure text into context
    return [{"role": r["role"], "content": r["content"],
             "images": _load_images(json.loads(r["images"] or "[]"))}
            for r in rows if r["content"] or not r["error"]]


def _usage_record(price: tuple[float, float] | None, usage: dict | None,
                  prompt_chars: int, completion_text: str) -> dict:
    pt = usage.get("prompt_tokens") if usage else None
    ct = usage.get("completion_tokens") if usage else None
    estimated = pt is None or ct is None
    pt = pt if pt is not None else estimate_tokens("x" * prompt_chars)
    ct = ct if ct is not None else estimate_tokens(completion_text)
    out = {"prompt_tokens": pt, "completion_tokens": ct,
           "cost": cost_of(price, pt, ct) if price else None, "estimated": estimated}
    if usage and usage.get("cache_read_tokens") is not None:
        out["cache_read_tokens"] = usage["cache_read_tokens"]
    return out


async def _run_extra_model(chat_id: str, provider: dict, api_key: str, model: str,
                           messages: list[dict], params: dict,
                           price: tuple[float, float] | None) -> tuple[str, dict]:
    """One parallel multi-model reply. Returns (provider:model, payload)."""
    async with httpx.AsyncClient() as client:
        text, usage = await complete_safe(provider, api_key, model, messages,
                                          params, client)
    tag = f"{provider['id']}:{model}"
    err = usage.pop("__error__", None) if usage else None
    if err:
        return tag, {"error": err, "message_id": None}
    con = db.connect()
    try:
        prompt_chars = sum(len(m["content"]) for m in messages)
        u = _usage_record(price, usage, prompt_chars, text)
        mid = new_id()
        con.execute("INSERT INTO messages (id, chat_id, role, content, model, "
                    "interrupted, prompt_tokens, completion_tokens, cost, created_at) "
                    "VALUES (?,?,?,?,?,0,?,?,?,?)",
                    (mid, chat_id, "assistant", text, model, u["prompt_tokens"],
                     u["completion_tokens"], u["cost"], now()))
        con.commit()
    finally:
        con.close()
    return tag, {"text": text, "message_id": mid, "usage": u, "error": None}


async def complete_safe(provider, api_key, model, messages, params, client):
    try:
        return await providers.complete(provider, api_key, model, messages, params, client)
    except (providers.ProviderError, httpx.HTTPError, json.JSONDecodeError,
            ValueError, KeyError) as e:
        return "", {"__error__": _scrub_msg(e, api_key)}


async def _stream_chat_response(chat_id: str, *, user_content: str | None,
                                images: list[dict] | None = None,
                                use_knowledge: bool | None = None,
                                variant_group: str | None = None) -> StreamingResponse:
    """Shared SSE generator for send-message, regenerate and edit-and-rerun."""
    con = db.connect()
    try:
        chat = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if not chat:
            raise HTTPException(404, "chat not found")
        provider = load_provider(con, chat["provider_id"])
        api_key = api_key_for(con, chat["provider_id"])
        if user_content is not None:
            con.execute("INSERT INTO messages (id, chat_id, role, content, images, "
                        "created_at) VALUES (?,?,?,?,?,?)",
                        (new_id(), chat_id, "user", user_content,
                         json.dumps(images or []), now()))
        else:  # regenerate: keep the trailing reply as an inactive variant
            last = con.execute("SELECT id, role, variant_group FROM messages "
                               "WHERE chat_id=? AND is_active=1 "
                               "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                               (chat_id,)).fetchone()
            if not last:
                raise HTTPException(400, "nothing to regenerate")
            if last["role"] == "assistant":
                grp = last["variant_group"] or variant_group or new_id()
                con.execute("UPDATE messages SET is_active=0, variant_group=? "
                            "WHERE id=?", (grp, last["id"]))
                variant_group = grp
        history = _history(con, chat_id)
        if chat["context_limit"]:
            history = history[-chat["context_limit"]:]
        is_first_exchange = len(history) <= 2
        messages = []
        system = chat["system_prompt"] or ""
        use_knowledge_final = (chat["use_knowledge"] if use_knowledge is None
                               else use_knowledge)
        knowledge_user = ""
        if use_knowledge_final:
            knowledge_user = next((m["content"] for m in reversed(history)
                                   if m["role"] == "user"), "")
        if get_setting(con, "memory_enabled") == "1":
            mems = [r["content"] for r in con.execute(
                "SELECT content FROM memories ORDER BY created_at LIMIT 200")]
            if mems:
                messages.append({"role": "system", "content":
                                 "Things you remember about the user "
                                 "(apply naturally, don't recite):\n- "
                                 + "\n- ".join(mems)})
        skill_ids = [r["skill_id"] for r in con.execute(
            "SELECT skill_id FROM agents_skills_link WHERE agent_id=?",
            (chat["agent_id"],))] if chat["agent_id"] else []
        for sid in skill_ids:
            srow = con.execute("SELECT name, content FROM skills WHERE id=?",
                               (sid,)).fetchone()
            if srow:
                messages.append({"role": "system", "content":
                                 f"## Skill: {srow['name']}\n{srow['content']}"})
        if chat["output_language"]:
            system = (system + "\n\n" if system else "") + \
                f"Always respond in {chat['output_language']}."
        if system:
            messages.append({"role": "system", "content": system})
        messages += history
        con.commit()
        params = {"temperature": chat["temperature"], "max_tokens": chat["max_tokens"],
                  "top_p": chat["top_p"], "frequency_penalty": chat["frequency_penalty"],
                  "presence_penalty": chat["presence_penalty"],
                  "reasoning_effort": chat["reasoning_effort"],
                  "cache_prompt": bool(chat["cache_prompt"])}
        title_was_default = chat["title"] == "New chat"
        p_provider, p_model = dict(provider), chat["model"]
        extra_models = json.loads(chat["extra_models"] or "[]")
        stream_mode = bool(chat["stream"])
        prompt_chars = sum(len(m["content"]) for m in messages)
        memory_on = get_setting(con, "memory_enabled") == "1"
    finally:
        con.close()

    async def save_assistant(text: str, usage: dict | None) -> dict:
        con = db.connect()
        try:
            price = price_for(con, p_model)
            u = _usage_record(price, usage, prompt_chars, text)
            mid = new_id()
            con.execute("INSERT INTO messages (id, chat_id, role, content, model, "
                        "interrupted, prompt_tokens, completion_tokens, cost, "
                        "variant_group, created_at) VALUES (?,?,?,?,?,0,?,?,?,?,?)",
                        (mid, chat_id, "assistant", text, p_model, u["prompt_tokens"],
                         u["completion_tokens"], u["cost"], variant_group, now()))
            con.execute("UPDATE chats SET updated_at=? WHERE id=?", (now(), chat_id))
            con.commit()
        finally:
            con.close()
        return {"message_id": mid, "usage": u}

    async def gen():
        partial: list[str] = []
        main_usage: dict = {}
        message_ids: list[str] = []
        con = db.connect()
        try:
            if use_knowledge_final and knowledge_user:
                # hybrid retrieval (FTS + optional embeddings) — own connection,
                # best-effort: a failure here must not kill the reply
                try:
                    kc = db.connect()
                    try:
                        snips = await hybrid_knowledge(kc, knowledge_user)
                    finally:
                        kc.close()
                    if snips:
                        messages.insert(0, {"role": "system", "content":
                                            "Context from the user's documents:\n"
                                            + "\n---\n".join(snips)})
                except Exception:
                    pass
            async with httpx.AsyncClient() as client:
                if stream_mode:
                    async for delta in providers.stream_chat(
                            p_provider, api_key, p_model, messages, params, client,
                            usage=main_usage):
                        partial.append(delta)
                        yield sse("delta", {"text": delta})
                else:
                    text, usage = await complete_safe(p_provider, api_key, p_model,
                                                      messages, params, client)
                    err = usage.pop("__error__", None) if usage else None
                    if err:
                        raise providers.ProviderError(err)
                    partial.append(text)
                    main_usage.update(usage or {})
                    yield sse("delta", {"text": text})
            saved = await save_assistant("".join(partial), main_usage or None)
            message_ids.append(saved["message_id"])
            yield sse("meta", {"model": f"{p_provider['id']}:{p_model}",
                               "message_id": saved["message_id"],
                               "usage": saved["usage"]})
            if extra_models:
                # each spec is its own "provider:model" pair with its own key
                specs = []
                for spec in extra_models:
                    pid, _, model = spec.partition(":")
                    if not (pid and model):
                        continue
                    try:
                        specs.append((model, load_provider(con, pid),
                                      api_key_for(con, pid), price_for(con, model)))
                    except HTTPException as e:
                        yield sse("error", {"message": e.detail, "model": spec})
                results = await asyncio.gather(*[
                    _run_extra_model(chat_id, ep, key, model, messages, params, price)
                    for model, ep, key, price in specs])
                for tag, payload in results:
                    if payload.get("error"):
                        yield sse("error", {"message": payload["error"], "model": tag})
                        continue
                    message_ids.append(payload["message_id"])
                    yield sse("meta", {"model": tag, "message_id": payload["message_id"],
                                       "usage": payload["usage"]})
                    yield sse("delta", {"text": payload["text"], "model": tag})
            con.execute("UPDATE chats SET updated_at=? WHERE id=?", (now(), chat_id))
            con.commit()
            yield sse("done", {"interrupted": False, "message_ids": message_ids})
            if title_was_default and is_first_exchange and "".join(partial).strip():
                task = asyncio.create_task(_generate_title(chat_id, p_provider, api_key,
                                                           p_model, history))
                _bg_tasks.add(task)
                task.add_done_callback(_bg_tasks.discard)
            if memory_on and "".join(partial).strip():
                mem_history = history + [{"role": "assistant",
                                          "content": "".join(partial)}]
                task = asyncio.create_task(_extract_memories(
                    p_provider, api_key, p_model, mem_history))
                _bg_tasks.add(task)
                task.add_done_callback(_bg_tasks.discard)
        except (providers.ProviderError, httpx.HTTPError, json.JSONDecodeError,
                ValueError, KeyError) as e:
            msg = _scrub_msg(e, api_key)
            # persist the failure (even with zero text) so it survives a reload
            con.execute("INSERT INTO messages (id, chat_id, role, content, model, "
                        "interrupted, error, created_at) VALUES (?,?,?,?,?,1,?,?)",
                        (new_id(), chat_id, "assistant", "".join(partial), p_model,
                         msg, now()))
            con.commit()
            yield sse("error", {"message": msg})
        except (asyncio.CancelledError, GeneratorExit):
            if partial:  # user pressed stop — keep the partial, no error text
                con.execute("INSERT INTO messages (id, chat_id, role, content, model, "
                            "interrupted, created_at) VALUES (?,?,?,?,?,1,?)",
                            (new_id(), chat_id, "assistant", "".join(partial), p_model, now()))
                con.commit()
            raise
        finally:
            con.close()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        "Connection": "keep-alive"})


def _scrub_msg(e: Exception, api_key: str) -> str:
    msg = str(e) or e.__class__.__name__
    return providers._scrub(msg, api_key)


async def _generate_title(chat_id: str, provider: dict, api_key: str, model: str,
                          history: list[dict]) -> None:
    """Best-effort title from the first exchange; all failures are silent."""
    try:
        convo = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in history[-4:])
        async with httpx.AsyncClient() as client:
            text, _usage = await providers.complete(provider, api_key, model, [
                {"role": "system",
                 "content": "Summarize this conversation in 3-6 words as a chat title. "
                            "Reply with the title only, no quotes."},
                {"role": "user", "content": convo}], {}, client)
        title = text.strip().strip('"').strip()[:300]
        if title:
            con = db.connect()
            try:
                con.execute("UPDATE chats SET title=?, updated_at=? WHERE id=? AND "
                            "title='New chat'", (title, now(), chat_id))
                con.commit()
            finally:
                con.close()
    except Exception:
        pass  # ponytail: title generation is cosmetic; never fail a chat over it


async def _extract_memories(provider: dict, api_key: str, model: str,
                            history: list[dict]) -> None:
    """Best-effort memory extraction after an exchange; silent on any failure."""
    try:
        con = db.connect()
        try:
            prompt = get_setting(con, "memory_extract_prompt") or _DEFAULT_MEMORY_PROMPT
        finally:
            con.close()
        convo = "\n".join(f"{m['role']}: {m['content'][:800]}" for m in history[-6:])
        async with httpx.AsyncClient() as client:
            text, _u = await providers.complete(provider, api_key, model, [
                {"role": "system", "content": prompt},
                {"role": "user", "content": convo}], {}, client)
        items = json.loads(_extract_json_array(text))
        if not isinstance(items, list):
            return
        con = db.connect()
        try:
            existing = {r["content"].strip().casefold() for r in con.execute(
                "SELECT content FROM memories")}
            count = con.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            ts = now()
            for item in items[:5]:
                if not isinstance(item, str) or not (4 <= len(item.strip()) <= 300):
                    continue
                content = item.strip()
                if content.casefold() in existing or count >= 200:
                    continue
                con.execute("INSERT INTO memories (id, content, source, created_at, "
                            "updated_at) VALUES (?,?,?,?,?)",
                            (new_id(), content, "auto", ts, ts))
                existing.add(content.casefold())
                count += 1
            con.commit()
        finally:
            con.close()
    except Exception:
        pass  # cosmetic background feature — never fail a chat over it


_DEFAULT_MEMORY_PROMPT = (
    "From the conversation below, extract durable facts about the user worth "
    "remembering for future chats (preferences, context, ongoing work, "
    "corrections like 'I prefer X'). Do NOT extract one-off task details, "
    "anything already visible in the conversation itself, or sensitive data "
    "(passwords, full names of third parties, financial details). Reply with "
    "ONLY a JSON array of short third-person statements, max 3 items, e.g. "
    '["Prefers concise answers with code first","Is building a FastAPI app"]. '
    "Reply with [] if there is nothing durable.")

_DEFAULT_ARTIFACT_REWRITE_PROMPT = (
    "You are rewriting an existing artifact (an HTML page, SVG image, Markdown "
    "document or source-code file) for the user. Apply the requested change "
    "faithfully and return the COMPLETE rewritten artifact. This is text "
    "editing only: never execute code, never call tools, never fetch network "
    "resources, and never add anything that would run outside this document. "
    "Preserve the artifact's format and language unless asked to change it, and "
    "keep unrelated parts byte-identical where possible. Output ONLY the "
    "artifact body — no preamble, no commentary, no surrounding code fences.")


def _extract_json_array(text: str) -> str:
    """Pull the first JSON array out of a model reply."""
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return "[]"
    return text[start:end + 1]


def _strip_fence(text: str) -> str:
    """Models often wrap artifact rewrites in ``` fences; unwrap when the whole
    reply is exactly one fenced block, else return the text trimmed."""
    m = re.fullmatch(r"\s*```[\w.+-]*\r?\n(.*?)\r?\n?```\s*", text or "", re.DOTALL)
    return m.group(1) if m else (text or "").strip()


@router.post("/chats/{chat_id}/messages")
async def send_message(chat_id: str, body: MessageIn):
    saved = [save_image(chat_id, u) for u in body.images] if body.images else []
    return await _stream_chat_response(chat_id, user_content=body.content,
                                       images=saved,
                                       use_knowledge=body.use_knowledge)


@router.post("/chats/{chat_id}/regenerate")
async def regenerate(chat_id: str):
    return await _stream_chat_response(chat_id, user_content=None)


# --- edit / fork / share --------------------------------------------------------

class MessagePatch(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


def _get_message(con: sqlite3.Connection, chat_id: str, message_id: str) -> sqlite3.Row:
    row = con.execute("SELECT *, rowid FROM messages WHERE id=? AND chat_id=?",
                      (message_id, chat_id)).fetchone()
    if not row:
        raise HTTPException(404, "message not found")
    return row


@router.patch("/chats/{chat_id}/messages/{message_id}")
def edit_message(chat_id: str, message_id: str, body: MessagePatch):
    """Edit any message in place (assistant 'canvas' edits, typos)."""
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone():
            raise HTTPException(404, "chat not found")
        _get_message(con, chat_id, message_id)
        con.execute("UPDATE messages SET content=? WHERE id=?", (body.content, message_id))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.post("/chats/{chat_id}/messages/{message_id}/rerun")
async def rerun_message(chat_id: str, message_id: str, body: MessagePatch):
    """Edit a user message, re-stream the reply from that point. When the tail
    is a single assistant reply it is kept as an inactive variant of the new
    one; longer tails are truncated as before."""
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone():
            raise HTTPException(404, "chat not found")
        row = _get_message(con, chat_id, message_id)
        if row["role"] != "user":
            raise HTTPException(400, "only user messages can be re-run")
        con.execute("UPDATE messages SET content=? WHERE id=?", (body.content, message_id))
        tail = con.execute(
            "SELECT id, role, rowid FROM messages WHERE chat_id=? AND is_active=1 "
            "AND rowid > (SELECT rowid FROM messages WHERE id=?) "
            "ORDER BY created_at, rowid", (chat_id, message_id)).fetchall()
        grp = None
        if len(tail) == 1 and tail[0]["role"] == "assistant":
            grp = new_id()
            con.execute("UPDATE messages SET is_active=0, variant_group=? WHERE id=?",
                        (grp, tail[0]["id"]))
        else:
            con.execute("DELETE FROM messages WHERE chat_id=? AND rowid > "
                        "(SELECT rowid FROM messages WHERE id=?)", (chat_id, message_id))
        con.commit()
    finally:
        con.close()
    return await _stream_chat_response(chat_id, user_content=None, variant_group=grp)


class ForkIn(BaseModel):
    message_id: str | None = None  # copy messages up to & including this one


@router.post("/chats/{chat_id}/fork")
def fork_chat(chat_id: str, body: ForkIn | None = None):
    """Copy a chat (optionally truncated at a message) into a new chat."""
    con = db.connect()
    try:
        row = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if not row:
            raise HTTPException(404, "chat not found")
        cutoff = None
        if body and body.message_id:
            cutoff = _get_message(con, chat_id, body.message_id)["rowid"]
        cid = new_id()
        ts = now()
        con.execute("INSERT INTO chats (id, title, folder_id, model, provider_id, "
                    "agent_id, temperature, max_tokens, top_p, system_prompt, pinned, "
                    "tags, archived, context_limit, frequency_penalty, presence_penalty, "
                    "stream, extra_models, use_knowledge, created_at, updated_at) "
                    "SELECT ?, title || ' (fork)', folder_id, model, provider_id, "
                    "agent_id, temperature, max_tokens, top_p, system_prompt, 0, tags, 0, "
                    "context_limit, frequency_penalty, presence_penalty, stream, "
                    "extra_models, use_knowledge, ?, ? FROM chats WHERE id=?",
                    (cid, ts, ts, chat_id))
        n = 0
        for m in con.execute("SELECT *, rowid FROM messages WHERE chat_id=? AND "
                             "is_active=1 ORDER BY created_at, rowid",
                             (chat_id,)).fetchall():
            if cutoff is not None and m["rowid"] > cutoff:
                break
            con.execute("INSERT INTO messages (id, chat_id, role, content, model, "
                        "interrupted, images, prompt_tokens, completion_tokens, cost, "
                        "error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (new_id(), cid, m["role"], m["content"], m["model"],
                         m["interrupted"], m["images"], m["prompt_tokens"],
                         m["completion_tokens"], m["cost"], m["error"], m["created_at"]))
            n += 1
        con.commit()
        return {"id": cid, "copied_messages": n}
    finally:
        con.close()


class ShareOut(BaseModel):
    url: str


@router.post("/chats/{chat_id}/share")
def share_chat(chat_id: str):
    """Create (or return) a secret read-only link. Capability URL: possession
    of the token grants read access; DELETE revokes."""
    con = db.connect()
    try:
        row0 = con.execute("SELECT temporary FROM chats WHERE id=?",
                           (chat_id,)).fetchone()
        if not row0:
            raise HTTPException(404, "chat not found")
        if row0["temporary"]:
            raise HTTPException(400, "temporary chats cannot be shared")
        row = con.execute("SELECT token FROM shares WHERE chat_id=?", (chat_id,)).fetchone()
        if row:
            return {"url": f"/s/{row['token']}"}
        token = secrets.token_urlsafe(24)
        con.execute("INSERT INTO shares (token, chat_id, created_at) VALUES (?,?,?)",
                    (token, chat_id, now()))
        con.commit()
        return {"url": f"/s/{token}"}
    finally:
        con.close()


@router.delete("/chats/{chat_id}/share")
def unshare_chat(chat_id: str):
    con = db.connect()
    try:
        con.execute("DELETE FROM shares WHERE chat_id=?", (chat_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.get("/public/{token}")
def public_chat(token: str):
    """Read-only chat content for a share token. Deliberately unauthenticated:
    the token is the secret. Exposes title + message text only."""
    con = db.connect()
    try:
        row = con.execute(
            "SELECT c.title FROM shares s JOIN chats c ON c.id = s.chat_id "
            "WHERE s.token=?", (token,)).fetchone()
        if not row:
            raise HTTPException(404, "share not found")
        msgs = con.execute(
            "SELECT m.role, m.content, m.model, m.created_at FROM messages m "
            "JOIN shares s ON s.chat_id = m.chat_id WHERE s.token=? "
            "ORDER BY m.created_at, m.rowid", (token,)).fetchall()
        return {"app": "opentyper", "title": row["title"],
                "messages": [dict(m) for m in msgs]}
    finally:
        con.close()


@router.get("/uploads/{filename}")
def serve_upload(filename: str):
    path = (Path(db.DATA_DIR) / "uploads" / Path(filename).name).resolve()
    uploads = (Path(db.DATA_DIR) / "uploads").resolve()
    if not path.is_relative_to(uploads) or not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type={
        "png": "image/png", "jpg": "image/jpeg", "webp": "image/webp",
        "gif": "image/gif"}.get(path.suffix.lstrip("."), "application/octet-stream"))


# --- text-to-speech (Edge neural voices) ------------------------------------------

@router.get("/tts/voices")
async def tts_voices():
    try:
        return await tts.edge_voices()
    except Exception as e:
        raise HTTPException(502, f"voice list unavailable: {e}") from e


@router.get("/tts")
async def tts_endpoint(text: str, voice: str = "en-US-AriaNeural"):
    """Synthesize speech (audio/mpeg) for the in-app player."""
    import re as _re
    text = text.strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > tts.MAX_TTS_CHARS:
        raise HTTPException(400, f"text too long (max {tts.MAX_TTS_CHARS} chars)")
    if not _re.fullmatch(r"[A-Za-z]{2,5}-[A-Za-z]{2,10}-[A-Za-z]{4,20}Neural", voice):
        raise HTTPException(400, "invalid voice id")
    try:
        audio = await tts.edge_speak(text, voice)
    except Exception as e:
        raise HTTPException(502, f"speech failed: {e}") from e
    return Response(audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})


# --- documents (knowledge base lite) ---------------------------------------------

class DocumentIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=2_000_000)


@router.get("/documents")
def list_documents():
    con = db.connect()
    try:
        return [{**dict(r), "size": len(r["content"])} for r in con.execute(
            "SELECT id, name, created_at, content FROM documents ORDER BY name")]
    finally:
        con.close()


@router.post("/documents")
def create_document(body: DocumentIn):
    con = db.connect()
    try:
        did = new_id()
        con.execute("INSERT INTO documents (id, name, content, created_at) "
                    "VALUES (?,?,?,?)", (did, body.name, body.content, now()))
        con.commit()
        return rowdict(con.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone())
    finally:
        con.close()


@router.delete("/documents/{document_id}")
def delete_document(document_id: str):
    con = db.connect()
    try:
        con.execute("DELETE FROM documents WHERE id=?", (document_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


# --- search ------------------------------------------------------------------

@router.get("/search")
def search(q: str, limit: int = 50):
    q = q.strip()
    if not q:
        return []
    limit = max(1, min(limit, 200))
    con = db.connect()
    try:
        try:
            rows = con.execute(
                "SELECT c.id AS chat_id, c.title, c.updated_at, m.id AS message_id, "
                "snippet(messages_fts, 0, '»', '«', '…', 16) AS snippet "
                "FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid "
                "JOIN chats c ON c.id = m.chat_id "
                "WHERE messages_fts MATCH ? AND m.is_active=1 AND c.temporary=0 "
                "ORDER BY rank LIMIT ?",
                ('"' + q.replace('"', '""') + '"', limit)).fetchall()
        except sqlite3.OperationalError:
            raise HTTPException(400, "invalid search query")
        return [dict(r) for r in rows]
    finally:
        con.close()


# --- settings / password -----------------------------------------------------

class SettingsIn(BaseModel):
    theme: str | None = Field(None, pattern="^(light|dark|system)$")
    enter_to_send: bool | None = None
    default_provider_id: str | None = None
    default_model: str | None = Field(None, max_length=200)
    model_prices: dict | None = None  # {"model-prefix": {"in": $/Mtok, "out": $/Mtok}}
    memory_enabled: bool | None = None
    memory_extract_prompt: str | None = Field(None, max_length=4000)
    finalize_prompt: str | None = Field(None, max_length=4000)
    artifact_rewrite_prompt: str | None = Field(None, max_length=4000)
    service_worker: bool | None = None
    embedding_provider_id: str | None = None
    embedding_model: str | None = Field(None, max_length=200)
    shortcuts: dict | None = None  # {"action": "Mod+K", …}


@router.get("/settings")
def get_settings():
    con = db.connect()
    try:
        emb_provider = get_setting(con, "embedding_provider_id")
        return {"theme": get_setting(con, "theme", "system"),
                "enter_to_send": get_setting(con, "enter_to_send", "1") == "1",
                "default_provider_id": get_setting(con, "default_provider_id"),
                "default_model": get_setting(con, "default_model"),
                "model_prices": json.loads(get_setting(con, "model_prices", "{}") or "{}"),
                "memory_enabled": get_setting(con, "memory_enabled", "0") == "1",
                "memory_extract_prompt": get_setting(con, "memory_extract_prompt"),
                "finalize_prompt": get_setting(con, "finalize_prompt"),
                "artifact_rewrite_prompt": get_setting(con, "artifact_rewrite_prompt"),
                "service_worker": get_setting(con, "service_worker", "0") == "1",
                "embedding_provider_id": emb_provider,
                "embedding_model": get_setting(con, "embedding_model"),
                "shortcuts": json.loads(get_setting(con, "shortcuts", "{}") or "{}"),
                "elevenlabs_hint": get_setting(con, "elevenlabs_hint"),
                "has_password": bool(get_setting(con, "app_password_hash"))}
    finally:
        con.close()


@router.put("/settings")
def put_settings(body: SettingsIn):
    con = db.connect()
    try:
        if body.default_provider_id:
            load_provider(con, body.default_provider_id)
        if body.embedding_provider_id:
            load_provider(con, body.embedding_provider_id)
        for key, value in [("theme", body.theme),
                           ("enter_to_send", None if body.enter_to_send is None
                            else ("1" if body.enter_to_send else "0")),
                           ("default_provider_id", body.default_provider_id),
                           ("default_model", body.default_model),
                           ("memory_enabled", None if body.memory_enabled is None
                            else ("1" if body.memory_enabled else "0")),
                           ("memory_extract_prompt", body.memory_extract_prompt),
                           ("finalize_prompt", body.finalize_prompt),
                           ("artifact_rewrite_prompt", body.artifact_rewrite_prompt),
                           ("service_worker", None if body.service_worker is None
                            else ("1" if body.service_worker else "0")),
                           ("embedding_provider_id", body.embedding_provider_id),
                           ("embedding_model", body.embedding_model)]:
            if value is not None:
                set_setting(con, key, value)
        if body.model_prices is not None:
            set_setting(con, "model_prices", json.dumps(body.model_prices))
        if body.shortcuts is not None:
            set_setting(con, "shortcuts", json.dumps(body.shortcuts))
        con.commit()
        return get_settings()
    finally:
        con.close()


class SecretIn(BaseModel):
    name: str = Field(pattern="^(elevenlabs)$")
    value: str = Field(min_length=1, max_length=500)


@router.put("/settings/secret")
def put_secret(body: SecretIn):
    """Encrypted at rest; only a masked hint is ever returned."""
    con = db.connect()
    try:
        set_setting(con, f"{body.name}_key_enc", crypto.encrypt_secret(body.value))
        set_setting(con, f"{body.name}_hint", crypto.key_hint(body.value))
        con.commit()
    finally:
        con.close()
    return {"ok": True, "hint": crypto.key_hint(body.value)}


def _secret(con: sqlite3.Connection, name: str) -> str:
    row = con.execute("SELECT value FROM settings WHERE key=?",
                      (f"{name}_key_enc",)).fetchone()
    if not row:
        raise HTTPException(409, f"no {name} key configured (Settings → General)")
    return crypto.decrypt_secret(row["value"])


class PasswordIn(BaseModel):
    password: str = Field(min_length=8, max_length=200)


@router.put("/settings/password")
def set_password(body: PasswordIn, response: Response):
    stored = crypto.hash_password(body.password)
    con = db.connect()
    try:
        set_setting(con, "app_password_hash", stored)
        con.commit()
    finally:
        con.close()
    response.set_cookie(crypto.SESSION_COOKIE, crypto.make_session_token(stored),
                        httponly=True, samesite="lax", max_age=crypto.SESSION_TTL, path="/")
    return {"ok": True}


@router.delete("/settings/password")
def clear_password():
    con = db.connect()
    try:
        con.execute("DELETE FROM settings WHERE key='app_password_hash'")
        con.commit()
    finally:
        con.close()
    return {"ok": True}


# --- export / import ---------------------------------------------------------

def _full_export(con: sqlite3.Connection, chat_ids: list[str] | None) -> dict:
    chat_rows = con.execute("SELECT * FROM chats WHERE temporary=0 "
                            "ORDER BY created_at").fetchall()
    chats = []
    for c in chat_rows:
        if chat_ids and c["id"] not in chat_ids:
            continue
        d = dict(c)
        d["pinned"] = bool(d["pinned"])
        d["messages"] = [dict(m) for m in con.execute(
            "SELECT id, role, content, model, interrupted, created_at FROM messages "
            "WHERE chat_id=? AND is_active=1 ORDER BY created_at, rowid",
            (c["id"],)).fetchall()]
        chats.append(d)
    return {
        "app": "opentyper", "version": 1, "exported_at": now(),
        "folders": [dict(r) for r in con.execute("SELECT * FROM folders").fetchall()],
        "prompts": [dict(r) for r in con.execute("SELECT * FROM prompts").fetchall()],
        "agents": [dict(r) for r in con.execute("SELECT * FROM agents").fetchall()],
        "chats": chats,
    }


@router.post("/export")
def export_data(body: dict):
    fmt = body.get("format", "json")
    chat_ids = body.get("chat_ids")
    if fmt not in ("json", "markdown", "html"):
        raise HTTPException(400, "format must be 'json', 'markdown' or 'html'")
    con = db.connect()
    try:
        if fmt == "json":
            doc = json.dumps(_full_export(con, chat_ids), indent=2)
            return Response(doc, media_type="application/json", headers={
                "Content-Disposition": 'attachment; filename="opentyper-export.json"'})
        chats = _full_export(con, chat_ids)["chats"]
        if fmt == "markdown":
            parts = []
            for c in chats:
                lines = [f"# {c['title']}", ""]
                for m in c["messages"]:
                    lines += [f"## {m['role']}", "", m["content"], ""]
                parts.append("\n".join(lines))
            doc = "\n\n---\n\n".join(parts)
            return Response(doc, media_type="text/markdown", headers={
                "Content-Disposition": 'attachment; filename="opentyper-export.md"'})
        # html: self-contained, printable to PDF from any browser
        def esc(s: str) -> str:
            return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        blocks = []
        for c in chats:
            blocks.append(f"<h1>{esc(c['title'])}</h1>")
            for m in c["messages"]:
                who = esc(m.get("model") if m["role"] == "assistant" else m["role"])
                blocks.append(f"<div class='m'><div class='who'>{who}</div>"
                              f"<pre>{esc(m['content'])}</pre></div>")
        doc = ("<!doctype html><meta charset='utf-8'><title>OpenTyper export</title>"
               "<style>body{font-family:system-ui;max-width:52rem;margin:2rem auto;"
               "padding:0 1rem;line-height:1.5}pre{white-space:pre-wrap;word-break:"
               "break-word;font:inherit;margin:.2rem 0 1.2rem}.who{font-weight:600;"
               "text-transform:uppercase;font-size:.72rem;color:#777}@media print{"
               "h1{page-break-before:always}h1:first-of-type{page-break-before:avoid}}"
               "</style>" + "\n".join(blocks))
        return Response(doc, media_type="text/html", headers={
            "Content-Disposition": 'attachment; filename="opentyper-export.html"'})
    finally:
        con.close()


class ImportDoc(BaseModel):
    model_config = {"extra": "ignore"}
    app: str | None = None
    source: str | None = None  # "opentyper" (default) | "chatgpt" | "claude"
    chats: list[dict] = []
    folders: list[dict] = []
    prompts: list[dict] = []
    agents: list[dict] = []


def _convert_chatgpt(doc: dict) -> dict:
    """Map a ChatGPT conversations.json export onto the OpenTyper schema.

    Conversations are reply-graphs (mapping of node id → message with parent
    links); we linearise the current branch, keeping user/assistant turns."""
    out = []
    convos = doc.get("conversations") if isinstance(doc, dict) else doc
    for c in convos or []:
        mapping = c.get("mapping") or {}
        node = c.get("current_node")
        chain: list[dict] = []
        while node and node in mapping:
            entry = mapping[node]
            msg = entry.get("message") or {}
            role = (msg.get("author") or {}).get("role")
            if role in ("user", "assistant"):
                parts = (msg.get("content") or {}).get("parts") or []
                text = "\n".join(str(p) for p in parts if isinstance(p, str)).strip()
                if text:
                    chain.append({"role": role, "content": text,
                                  "created_at": _ts_iso(msg.get("create_time"))})
            node = entry.get("parent")
        chain.reverse()
        if chain:
            out.append({"title": c.get("title") or "imported ChatGPT chat",
                        "model": (mapping.get(c.get("current_node"), {})
                                  .get("message", {}).get("metadata", {})
                                  .get("model_slug")) or "unknown",
                        "messages": chain})
    return {"app": "opentyper", "source": "chatgpt", "chats": out}


def _convert_claude(doc: dict) -> dict:
    """Map a Claude data export (chats.json) onto the OpenTyper schema."""
    out = []
    chats = doc.get("chats") if isinstance(doc, dict) else doc
    for c in chats or []:
        msgs = []
        for m in c.get("chat_messages") or []:
            role = {"human": "user", "assistant": "assistant"}.get(m.get("sender"))
            if not role:
                continue
            content = m.get("text")
            if not content and isinstance(m.get("content"), list):
                content = "\n".join(p.get("text", "") for p in m["content"]
                                    if isinstance(p, dict))
            if isinstance(content, str) and content.strip():
                msgs.append({"role": role, "content": content.strip(),
                             "created_at": _ts_iso(m.get("created_at"))})
        if msgs:
            out.append({"title": c.get("name") or "imported Claude chat",
                        "model": c.get("model") or "unknown", "messages": msgs})
    return {"app": "opentyper", "source": "claude", "chats": out}


def _ts_iso(value) -> str | None:
    """Best-effort epoch float/ISO string → ISO 8601 UTC."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        from datetime import datetime, timezone
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc) \
                .isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)[:19]


@router.post("/import")
def import_data(raw: dict):
    """Import OpenTyper JSON, or convert a ChatGPT conversations.json /
    Claude chats.json export (auto-detected, or forced via source)."""
    source = raw.get("source")
    if not source:
        if "conversations" in raw:
            source = "chatgpt"
        elif isinstance(raw.get("chats"), list) and raw["chats"] and \
                isinstance(raw["chats"][0], dict) and \
                "chat_messages" in raw["chats"][0]:
            source = "claude"
        else:
            source = "opentyper"
    if source == "chatgpt":
        body = ImportDoc(**_convert_chatgpt(raw))
    elif source == "claude":
        body = ImportDoc(**_convert_claude(raw))
    elif source == "opentyper":
        body = ImportDoc(**raw)
    else:
        raise HTTPException(400, "source must be 'opentyper', 'chatgpt' or 'claude'")
    con = db.connect()
    try:
        ts = now()
        known_providers = {r["id"] for r in con.execute("SELECT id FROM providers")}
        for f in body.folders:
            con.execute("INSERT OR REPLACE INTO folders (id, name, provider_id, model, "
                        "agent_id, system_prompt, position, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,COALESCE(?,0),COALESCE(?,?),?)",
                        (f.get("id") or new_id(), f.get("name", "imported"),
                         f.get("provider_id"), f.get("model"), f.get("agent_id"),
                         f.get("system_prompt"), f.get("position"),
                         f.get("created_at"), ts, ts))
        for a in body.agents:
            con.execute("INSERT OR REPLACE INTO agents (id, name, description, "
                        "system_prompt, model, provider_id, temperature, max_tokens, "
                        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,COALESCE(?,?),?)",
                        (a.get("id") or new_id(), a.get("name", "imported"),
                         a.get("description", ""), a.get("system_prompt", ""),
                         a.get("model"),
                         a.get("provider_id") if a.get("provider_id") in known_providers else None,
                         a.get("temperature"), a.get("max_tokens"),
                         a.get("created_at"), ts, ts))
        for p in body.prompts:
            tags = p.get("tags", "[]")
            if not isinstance(tags, str):
                tags = json.dumps(tags)
            con.execute("INSERT OR REPLACE INTO prompts (id, title, description, "
                        "content, tags, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                        (p.get("id") or new_id(), p.get("title", "imported"),
                         p.get("description", ""), p.get("content", ""),
                         tags, p.get("created_at"), ts))
        known_folders = {r["id"] for r in con.execute("SELECT id FROM folders")}
        known_agents = {r["id"] for r in con.execute("SELECT id FROM agents")}
        for c in body.chats:
            cid = c.get("id") or new_id()
            provider_id = c.get("provider_id")
            if provider_id not in known_providers:
                provider_id = "openai"
            con.execute("INSERT OR REPLACE INTO chats (id, title, folder_id, model, "
                        "provider_id, agent_id, temperature, max_tokens, top_p, "
                        "system_prompt, pinned, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, c.get("title", "imported"),
                         c.get("folder_id") if c.get("folder_id") in known_folders else None,
                         c.get("model", "unknown"), provider_id,
                         c.get("agent_id") if c.get("agent_id") in known_agents else None,
                         c.get("temperature"), c.get("max_tokens"), c.get("top_p"),
                         c.get("system_prompt"), 1 if c.get("pinned") else 0,
                         c.get("created_at") or ts, ts))
            for i, m in enumerate(c.get("messages", [])):
                role = m.get("role")
                if role not in ("user", "assistant") or not isinstance(m.get("content"), str):
                    continue
                con.execute("INSERT OR REPLACE INTO messages (id, chat_id, role, "
                            "content, model, interrupted, error, created_at) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (m.get("id") or f"{cid}-{i}", cid, role, m["content"],
                             m.get("model"), 1 if m.get("interrupted") else 0,
                             m.get("error"), m.get("created_at") or ts))
        con.commit()
        counts = {"chats": len(body.chats), "folders": len(body.folders),
                  "prompts": len(body.prompts), "agents": len(body.agents)}
        return {"ok": True, **counts}
    finally:
        con.close()


# --- message management / variants -------------------------------------------

@router.delete("/chats/{chat_id}/messages/{message_id}")
def delete_message(chat_id: str, message_id: str):
    """Delete one message. Variants in its group stay — they are independent
    rows; deleting an active message just removes it from the thread."""
    con = db.connect()
    try:
        _get_message(con, chat_id, message_id)
        con.execute("DELETE FROM messages WHERE id=?", (message_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


class ActivateIn(BaseModel):
    active: bool = True


@router.post("/chats/{chat_id}/messages/{message_id}/activate")
def activate_variant(chat_id: str, message_id: str, body: ActivateIn):
    """Switch which reply in a variant group is the live one (threads-lite)."""
    con = db.connect()
    try:
        row = _get_message(con, chat_id, message_id)
        grp = row["variant_group"]
        if grp:
            con.execute("UPDATE messages SET is_active=0 WHERE variant_group=? "
                        "AND chat_id=?", (grp, chat_id))
        con.execute("UPDATE messages SET is_active=? WHERE id=?",
                    (1 if body.active else 0, message_id))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


# --- shares list ----------------------------------------------------------------

@router.get("/shares")
def list_shares():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute(
            "SELECT s.token, s.chat_id, s.created_at, c.title FROM shares s "
            "JOIN chats c ON c.id = s.chat_id ORDER BY s.created_at DESC")]
    finally:
        con.close()


# --- finalize (multi-model synthesis) -------------------------------------------

class FinalizeIn(BaseModel):
    instruction: str | None = Field(None, max_length=4000)
    provider_id: str | None = None
    model: str | None = Field(None, max_length=200)


@router.post("/chats/{chat_id}/finalize")
async def finalize_chat(chat_id: str, body: FinalizeIn):
    """Synthesize the parallel replies of the latest exchange into one answer.
    Non-streaming (single request); result is persisted as an assistant
    message tagged model 'finalize · <model>'."""
    con = db.connect()
    try:
        chat = con.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if not chat:
            raise HTTPException(404, "chat not found")
        rows = con.execute(
            "SELECT role, content FROM messages WHERE chat_id=? AND is_active=1 "
            "ORDER BY created_at, rowid", (chat_id,)).fetchall()
        # replies after the last user message = the parallel fan-out answers
        replies, seen_user = [], False
        for r in reversed(rows):
            if r["role"] == "user":
                seen_user = True
                break
            if r["role"] == "assistant" and r["content"]:
                replies.append(r["content"])
        if not seen_user or not replies:
            raise HTTPException(400, "no replies to finalize — send a message first")
        provider = load_provider(con, body.provider_id or chat["provider_id"])
        api_key = api_key_for(con, provider["id"])
        model = body.model or chat["model"]
        instruction = (body.instruction or get_setting(con, "finalize_prompt")
                       or "Synthesize these AI replies into one best answer. "
                          "Keep the strongest points from each, resolve "
                          "conflicts, reply in Markdown, no meta commentary.")
        convo = "\n\n".join(f"--- Reply {i + 1} ---\n{t}"
                            for i, t in enumerate(reversed(replies)))
        price = price_for(con, model)
        prompt_chars = len(convo) + len(instruction)
    finally:
        con.close()
    messages = [{"role": "system", "content": instruction},
                {"role": "user", "content": convo}]
    async with httpx.AsyncClient() as client:
        text, usage = await complete_safe(provider, api_key, model, messages,
                                          {"temperature": 0.3}, client)
    err = usage.pop("__error__", None) if usage else None
    if err:
        raise HTTPException(502, err)
    con = db.connect()
    try:
        u = _usage_record(price, usage, prompt_chars, text)
        mid = new_id()
        con.execute("INSERT INTO messages (id, chat_id, role, content, model, "
                    "interrupted, prompt_tokens, completion_tokens, cost, created_at) "
                    "VALUES (?,?,?,?,?,0,?,?,?,?)",
                    (mid, chat_id, "assistant", text, f"finalize · {model}",
                     u["prompt_tokens"], u["completion_tokens"], u["cost"], now()))
        con.execute("UPDATE chats SET updated_at=? WHERE id=?", (now(), chat_id))
        con.commit()
        return {"message_id": mid, "text": text, "usage": u}
    finally:
        con.close()


# --- side chat (one-shot "Ask…" panel, never persisted) --------------------------

class SideChatIn(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)
    context: str | None = Field(None, max_length=100_000)
    provider_id: str | None = None
    model: str | None = Field(None, max_length=200)


@router.post("/side-chat")
async def side_chat(body: SideChatIn):
    """Streamed one-shot reply with no chat record — the floating panel uses
    it for 'explain/rewrite this selection' style questions."""
    con = db.connect()
    try:
        provider = load_provider(con, body.provider_id or "openai")
        api_key = api_key_for(con, provider["id"])
        model = body.model or (provider["models"] or [""])[0]
        if not model:
            raise HTTPException(400, "no model available for side chat")
    finally:
        con.close()

    async def gen():
        messages = []
        if body.context:
            messages.append({"role": "system", "content":
                             "The user selected this text in their chat app:\n\n"
                             + body.context[:50_000]})
        messages.append({"role": "user", "content": body.content})
        partial: list[str] = []
        async with httpx.AsyncClient() as client:
            try:
                async for delta in providers.stream_chat(
                        provider, api_key, model, messages, {}, client):
                    partial.append(delta)
                    yield sse("delta", {"text": delta})
            except (providers.ProviderError, httpx.HTTPError) as e:
                yield sse("error", {"message": _scrub_msg(e, api_key)})
                return
        yield sse("done", {"model": f"{provider['id']}:{model}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- memories -----------------------------------------------------------------

class MemoryIn(BaseModel):
    content: str = Field(min_length=4, max_length=300)


@router.get("/memories")
def list_memories():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM memories ORDER BY created_at DESC")]
    finally:
        con.close()


@router.post("/memories")
def create_memory(body: MemoryIn):
    con = db.connect()
    try:
        mid = new_id()
        ts = now()
        con.execute("INSERT INTO memories (id, content, source, created_at, "
                    "updated_at) VALUES (?,?,?,?,?)",
                    (mid, body.content.strip(), "user", ts, ts))
        con.commit()
        return rowdict(con.execute("SELECT * FROM memories WHERE id=?",
                                   (mid,)).fetchone())
    finally:
        con.close()


@router.patch("/memories/{memory_id}")
def update_memory(memory_id: str, body: MemoryIn):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM memories WHERE id=?",
                           (memory_id,)).fetchone():
            raise HTTPException(404, "memory not found")
        con.execute("UPDATE memories SET content=?, updated_at=? WHERE id=?",
                    (body.content.strip(), now(), memory_id))
        con.commit()
        return rowdict(con.execute("SELECT * FROM memories WHERE id=?",
                                   (memory_id,)).fetchone())
    finally:
        con.close()


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: str):
    con = db.connect()
    try:
        con.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


# --- skills (instructions-only, following the open SKILL.md pattern) ----------

class SkillIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)


@router.get("/skills")
def list_skills():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM skills ORDER BY name")]
    finally:
        con.close()


@router.post("/skills")
def create_skill(body: SkillIn):
    con = db.connect()
    try:
        sid = new_id()
        ts = now()
        con.execute("INSERT INTO skills (id, name, content, created_at, updated_at) "
                    "VALUES (?,?,?,?,?)", (sid, body.name, body.content, ts, ts))
        con.commit()
        return rowdict(con.execute("SELECT * FROM skills WHERE id=?", (sid,)).fetchone())
    finally:
        con.close()


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: str):
    con = db.connect()
    try:
        con.execute("DELETE FROM skills WHERE id=?", (skill_id,))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


class SkillImportIn(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    data: str  # base64


@router.post("/skills/import")
async def import_skill(body: SkillImportIn):
    """Import a skill from a SKILL.md file or a .zip containing one (sent as
    base64 — keeps the dependency tree free of multipart parsers). Skills are
    procedural instructions: the body is inlined into agent prompts, never
    executed."""
    import io
    import zipfile
    filename = body.filename
    try:
        file_bytes = base64.b64decode(body.data)
    except (ValueError, binascii.Error) as e:
        raise HTTPException(400, "invalid base64 payload") from e
    name, content = None, None
    if filename.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(file_bytes))
        except zipfile.BadZipFile:
            raise HTTPException(400, "not a valid zip file")
        candidates = [n for n in zf.namelist()
                      if n.lower().endswith("skill.md")
                      and not n.startswith("__MACOSX")]
        if not candidates:
            raise HTTPException(400, "no SKILL.md found in the archive")
        content = zf.read(candidates[0]).decode("utf-8", "replace")
        parent = candidates[0].rsplit("/", 1)[0] if "/" in candidates[0] else ""
        readme = next((n for n in zf.namelist() if n != candidates[0]
                       and n.startswith(parent + "/") and
                       n.lower().endswith((".md", ".txt"))), None)
        if readme:
            extra = zf.read(readme).decode("utf-8", "replace")
            if len(extra) + len(content) < 200_000:
                content += "\n\n" + extra
    elif filename.lower().endswith((".md", ".markdown", ".txt")):
        content = file_bytes.decode("utf-8", "replace")
    else:
        raise HTTPException(400, "upload a SKILL.md or a .zip containing one")
    if not content.strip():
        raise HTTPException(400, "skill file is empty")
    for line in content.splitlines():
        if line.startswith("# "):
            name = line[2:].strip()[:200]
            break
    con = db.connect()
    try:
        sid = new_id()
        ts = now()
        con.execute("INSERT INTO skills (id, name, content, created_at, updated_at) "
                    "VALUES (?,?,?,?,?)", (sid, name or filename, content, ts, ts))
        con.commit()
        return rowdict(con.execute("SELECT * FROM skills WHERE id=?", (sid,)).fetchone())
    finally:
        con.close()


class AgentSkillsIn(BaseModel):
    skill_ids: list[str] = []


@router.put("/agents/{agent_id}/skills")
def set_agent_skills(agent_id: str, body: AgentSkillsIn):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone():
            raise HTTPException(404, "agent not found")
        known = {r["id"] for r in con.execute("SELECT id FROM skills")}
        con.execute("DELETE FROM agents_skills_link WHERE agent_id=?", (agent_id,))
        for sid in dict.fromkeys(body.skill_ids):
            if sid in known:
                con.execute("INSERT OR IGNORE INTO agents_skills_link (agent_id, "
                            "skill_id) VALUES (?,?)", (agent_id, sid))
        con.commit()
        return {"ok": True, "skill_ids": body.skill_ids}
    finally:
        con.close()


@router.get("/agents/{agent_id}/skills")
def get_agent_skills(agent_id: str):
    con = db.connect()
    try:
        return [r["skill_id"] for r in con.execute(
            "SELECT skill_id FROM agents_skills_link WHERE agent_id=?", (agent_id,))]
    finally:
        con.close()


# --- artifacts (versioned html/svg/markdown/code documents per chat) ------------

class ArtifactIn(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_ARTIFACT_CHARS)
    title: str | None = Field(None, max_length=200)
    kind: str | None = Field(None, pattern="^(html|svg|markdown|code)$")
    language: str | None = Field(None, max_length=40)
    message_id: str | None = None


class ArtifactSaveIn(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_ARTIFACT_CHARS)
    title: str | None = Field(None, max_length=200)


class ArtifactRewriteIn(BaseModel):
    instruction: str | None = Field(None, max_length=4000)
    provider_id: str | None = None
    model: str | None = Field(None, max_length=200)


def _artifact_title(content: str, kind: str, title: str | None) -> str:
    if title and title.strip():
        return title.strip()[:200]
    for line in content.splitlines():
        if line.strip():
            return line.strip()[:80]
    return f"{kind} artifact"


def _load_artifact(con: sqlite3.Connection, artifact_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM artifacts WHERE id=?",
                      (artifact_id,)).fetchone()
    if not row:
        raise HTTPException(404, "artifact not found")
    return row


def _insert_artifact_version(con: sqlite3.Connection, prev: sqlite3.Row,
                             content: str, title: str | None) -> dict:
    """Save content as the next version of prev's chain. UNIQUE(root_id, version)
    makes concurrent saves safe: the loser retries with the fresh max; a third
    collision surfaces as 409 instead of lost or duplicated versions."""
    title = (title or prev["title"])[:200]
    for _ in range(3):
        try:
            v = con.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM artifacts "
                            "WHERE root_id=?", (prev["root_id"],)).fetchone()[0]
            aid = new_id()
            con.execute(
                "INSERT INTO artifacts (id, root_id, chat_id, message_id, title, "
                "kind, language, content, version, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (aid, prev["root_id"], prev["chat_id"], prev["message_id"], title,
                 prev["kind"], prev["language"], content, v, now()))
            con.commit()
            return dict(con.execute("SELECT * FROM artifacts WHERE id=?",
                                    (aid,)).fetchone())
        except sqlite3.IntegrityError:
            con.rollback()
    raise HTTPException(409, "artifact is being edited — try again")


@router.post("/chats/{chat_id}/artifacts")
def create_artifact(chat_id: str, body: ArtifactIn):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone():
            raise HTTPException(404, "chat not found")
        if body.message_id and not con.execute(
                "SELECT 1 FROM messages WHERE id=? AND chat_id=?",
                (body.message_id, chat_id)).fetchone():
            raise HTTPException(404, "message not found")
        kind = body.kind or "code"
        aid = new_id()
        con.execute(
            "INSERT INTO artifacts (id, root_id, chat_id, message_id, title, "
            "kind, language, content, version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (aid, aid, chat_id, body.message_id,
             _artifact_title(body.content, kind, body.title), kind,
             (body.language or "").lower() or None, body.content, 1, now()))
        con.commit()
        return dict(con.execute("SELECT * FROM artifacts WHERE id=?", (aid,)).fetchone())
    finally:
        con.close()


@router.get("/chats/{chat_id}/artifacts")
def list_artifacts(chat_id: str):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM chats WHERE id=?", (chat_id,)).fetchone():
            raise HTTPException(404, "chat not found")
        # exactly one max() aggregate → SQLite takes the bare columns from the
        # row that produced it (sqlite.org/lang_select.html#bareagg)
        rows = con.execute(
            "SELECT a.id, a.root_id, a.chat_id, a.message_id, a.title, a.kind, "
            "a.language, a.content, MAX(a.version) AS version, a.created_at, "
            "(SELECT COUNT(*) FROM artifacts c WHERE c.root_id = a.root_id) "
            "  AS versions "
            "FROM artifacts a WHERE a.chat_id=? GROUP BY a.root_id "
            "ORDER BY a.created_at DESC", (chat_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str):
    con = db.connect()
    try:
        return dict(_load_artifact(con, artifact_id))
    finally:
        con.close()


@router.get("/artifacts/{artifact_id}/versions")
def artifact_versions(artifact_id: str):
    con = db.connect()
    try:
        row = _load_artifact(con, artifact_id)
        return [dict(r) for r in con.execute(
            "SELECT id, version, title, kind, language, LENGTH(content) AS size, "
            "created_at FROM artifacts WHERE root_id=? ORDER BY version",
            (row["root_id"],))]
    finally:
        con.close()


@router.put("/artifacts/{artifact_id}")
def save_artifact(artifact_id: str, body: ArtifactSaveIn):
    """Saving always creates a new version — history is never rewritten."""
    con = db.connect()
    try:
        row = _load_artifact(con, artifact_id)
        return _insert_artifact_version(con, row, body.content, body.title)
    finally:
        con.close()


@router.delete("/artifacts/{artifact_id}")
def delete_artifact(artifact_id: str):
    con = db.connect()
    try:
        row = _load_artifact(con, artifact_id)
        n = con.execute("SELECT COUNT(*) FROM artifacts WHERE root_id=?",
                        (row["root_id"],)).fetchone()[0]
        con.execute("DELETE FROM artifacts WHERE root_id=?", (row["root_id"],))
        con.execute("DELETE FROM artifact_shares WHERE root_id=?", (row["root_id"],))
        con.commit()
        return {"ok": True, "deleted": n}
    finally:
        con.close()


@router.post("/artifacts/{artifact_id}/rewrite")
async def rewrite_artifact(artifact_id: str, body: ArtifactRewriteIn):
    """One-shot AI rewrite via the artifact's chat model; the result lands as a
    new version (never clobbers). Non-streaming, like finalize."""
    con = db.connect()
    try:
        row = _load_artifact(con, artifact_id)
        if len(row["content"]) > MAX_ARTIFACT_REWRITE_CHARS:
            raise HTTPException(400, f"artifact too large to rewrite "
                                     f"(max {MAX_ARTIFACT_REWRITE_CHARS} chars)")
        chat = con.execute("SELECT * FROM chats WHERE id=?",
                           (row["chat_id"],)).fetchone()
        if not chat:
            raise HTTPException(404, "chat not found")
        provider = load_provider(con, body.provider_id or chat["provider_id"])
        api_key = api_key_for(con, provider["id"])
        model = body.model or chat["model"]
        price = price_for(con, model)
        policy = (get_setting(con, "artifact_rewrite_prompt")
                  or _DEFAULT_ARTIFACT_REWRITE_PROMPT)
    finally:
        con.close()
    # the policy carries the no-execution/no-fence invariants; a custom
    # instruction refines it rather than replacing it
    system = policy
    if body.instruction:
        system += "\n\nThe user's instruction for this rewrite: " + body.instruction
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content":
            f"Rewrite this {row['kind']} artifact"
            + (f" ({row['language']})" if row["language"] else "")
            + ".\n\nCurrent artifact:\n\n" + row["content"]
            + "\n\nReturn the complete rewritten artifact only."},
    ]
    prompt_chars = len(system) + len(messages[1]["content"])
    async with httpx.AsyncClient() as client:
        text, usage = await complete_safe(provider, api_key, model, messages,
                                          {"temperature": 0.3}, client)
    err = usage.pop("__error__", None) if usage else None
    if err:
        raise HTTPException(502, err)
    text = _strip_fence(text)
    if not text.strip():
        raise HTTPException(502, "empty rewrite")
    if len(text) > MAX_ARTIFACT_CHARS:
        raise HTTPException(400, f"rewrite too long (max {MAX_ARTIFACT_CHARS} chars)")
    con = db.connect()
    try:
        saved = _insert_artifact_version(con, row, text, None)
        u = _usage_record(price, usage, prompt_chars, text)
        return {"artifact": saved, "usage": u}
    finally:
        con.close()


@router.post("/artifacts/{artifact_id}/share")
def share_artifact(artifact_id: str):
    """Create (or return) a secret read-only link to the artifact's latest version."""
    con = db.connect()
    try:
        row = _load_artifact(con, artifact_id)
        chat = con.execute("SELECT temporary FROM chats WHERE id=?",
                           (row["chat_id"],)).fetchone()
        if chat and chat["temporary"]:
            raise HTTPException(400, "temporary chats cannot be shared")
        existing = con.execute("SELECT token FROM artifact_shares WHERE root_id=?",
                               (row["root_id"],)).fetchone()
        if existing:
            return {"url": f"/pa/{existing['token']}"}
        token = secrets.token_urlsafe(24)
        con.execute("INSERT INTO artifact_shares (token, root_id, created_at) "
                    "VALUES (?,?,?)", (token, row["root_id"], now()))
        con.commit()
        return {"url": f"/pa/{token}"}
    finally:
        con.close()


@router.delete("/artifacts/{artifact_id}/share")
def unshare_artifact(artifact_id: str):
    con = db.connect()
    try:
        row = _load_artifact(con, artifact_id)
        con.execute("DELETE FROM artifact_shares WHERE root_id=?", (row["root_id"],))
        con.commit()
        return {"ok": True}
    finally:
        con.close()


@router.get("/public/artifact/{token}")
def public_artifact(token: str):
    """Read-only artifact content for a share token. Deliberately unauthenticated:
    the token is the secret. Serves the latest version of the shared root."""
    con = db.connect()
    try:
        row = con.execute(
            "SELECT a.title, a.kind, a.language, a.content, a.version, a.created_at "
            "FROM artifact_shares s JOIN artifacts a ON a.root_id = s.root_id "
            "WHERE s.token=? ORDER BY a.version DESC LIMIT 1", (token,)).fetchone()
        if not row:
            raise HTTPException(404, "share not found")
        return {"app": "opentyper", **dict(row)}
    finally:
        con.close()


# --- usage dashboard ------------------------------------------------------------

@router.get("/stats")
def usage_stats(days: int = 30):
    days = max(1, min(days, 365))
    con = db.connect()
    try:
        rows = con.execute(
            "SELECT substr(m.created_at, 1, 10) AS day, c.provider_id, m.model, "
            "SUM(m.prompt_tokens) AS tin, SUM(m.completion_tokens) AS tout, "
            "SUM(m.cost) AS cost, COUNT(*) AS n "
            "FROM messages m JOIN chats c ON c.id = m.chat_id "
            "WHERE m.role='assistant' AND m.error IS NULL AND c.temporary=0 "
            "GROUP BY day, c.provider_id, m.model ORDER BY day").fetchall()
        by_day: dict[str, dict] = {}
        by_model: dict[str, dict] = {}
        tot = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "messages": 0}
        for r in rows:
            if r["day"] < _days_ago(days):
                continue
            d = by_day.setdefault(r["day"], {"tokens_in": 0, "tokens_out": 0,
                                             "cost": 0.0, "messages": 0})
            d["tokens_in"] += r["tin"] or 0
            d["tokens_out"] += r["tout"] or 0
            d["cost"] += r["cost"] or 0
            d["messages"] += r["n"]
            m = by_model.setdefault(f"{r['provider_id']}:{r['model']}",
                                    {"provider_id": r["provider_id"], "model": r["model"],
                                     "tokens_in": 0, "tokens_out": 0, "cost": 0.0,
                                     "messages": 0})
            for src, dst in (("tin", "tokens_in"), ("tout", "tokens_out")):
                m[dst] += r[src] or 0
            m["cost"] += r["cost"] or 0
            m["messages"] += r["n"]
            tot["prompt_tokens"] += r["tin"] or 0
            tot["completion_tokens"] += r["tout"] or 0
            tot["cost"] += r["cost"] or 0
            tot["messages"] += r["n"]
        return {"days": [{"date": k, **v} for k, v in sorted(by_day.items())],
                "by_model": sorted(by_model.values(),
                                   key=lambda m: -m["tokens_in"] - m["tokens_out"]),
                "totals": tot}
    finally:
        con.close()


def _days_ago(n: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()


# --- image generation ------------------------------------------------------------

class ImageGenIn(BaseModel):
    provider_id: str
    model: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=4000)
    chat_id: str | None = None
    size: str | None = Field(None, pattern=r"^\d{3,4}x\d{3,4}$")


@router.post("/images/generate")
async def generate_image(body: ImageGenIn):
    """Generate an image via an OpenAI-compatible /images/generations endpoint
    or a Gemini image model; store it under uploads and (optionally) drop it
    into the chat as a new assistant message."""
    con = db.connect()
    try:
        provider = load_provider(con, body.provider_id)
        api_key = api_key_for(con, body.provider_id)
    finally:
        con.close()
    async with httpx.AsyncClient(timeout=180.0) as client:
        if provider["kind"] == "google":
            url = (f"{_gb(provider)}/models/{body.model}:generateContent")
            resp = await client.post(url, json={
                "contents": [{"parts": [{"text": body.prompt}]}],
                "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}},
                headers=providers._headers(provider, api_key))
            if resp.status_code >= 400:
                raise HTTPException(502, providers._scrub(
                    f"image generation failed: {resp.status_code}: "
                    f"{resp.text[:300]}", api_key))
            parts = (resp.json().get("candidates", [{}])[0].get("content", {})
                     .get("parts", []))
            img = next((p["inlineData"] for p in parts if "inlineData" in p), None)
            if not img:
                raise HTTPException(502, "model returned no image")
            mime, b64 = img["mimeType"], img["data"]
        else:
            payload = {"model": body.model, "prompt": body.prompt, "n": 1}
            if body.size:
                payload["size"] = body.size
            resp = await client.post(
                (provider["base_url"] or "").rstrip("/") + "/images/generations",
                json=payload, headers=providers._headers(provider, api_key))
            if resp.status_code >= 400:
                raise HTTPException(502, providers._scrub(
                    f"image generation failed: {resp.status_code}: "
                    f"{resp.text[:300]}", api_key))
            item = (resp.json().get("data") or [{}])[0]
            if item.get("b64_json"):
                mime, b64 = "image/png", item["b64_json"]
            elif item.get("url"):
                dl = await client.get(item["url"])
                dl.raise_for_status()
                mime = dl.headers.get("content-type", "image/png").split(";")[0]
                b64 = base64.b64encode(dl.content).decode()
            else:
                raise HTTPException(502, "image response had no data")
    raw = base64.b64decode(b64)
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "generated image too large to store")
    ext = IMAGE_MIMES.get(mime, "png")
    name = f"gen_{new_id()}.{ext}"
    (Path(db.DATA_DIR) / "uploads" / name).write_bytes(raw)
    ref = {"file": name, "mime": mime}
    out = {"url": f"/api/uploads/{name}", "file": name}
    if body.chat_id:
        con = db.connect()
        try:
            if not con.execute("SELECT 1 FROM chats WHERE id=?",
                               (body.chat_id,)).fetchone():
                raise HTTPException(404, "chat not found")
            mid = new_id()
            con.execute("INSERT INTO messages (id, chat_id, role, content, model, "
                        "images, created_at) VALUES (?,?,?,?,?,?,?)",
                        (mid, body.chat_id, "assistant", f"Generated image: {body.prompt}",
                         f"{body.provider_id}:{body.model}",
                         json.dumps([ref]), now()))
            con.execute("UPDATE chats SET updated_at=? WHERE id=?",
                        (now(), body.chat_id))
            con.commit()
            out["message_id"] = mid
        finally:
            con.close()
    return out


def _gb(provider: dict) -> str:
    return (provider.get("base_url") or "").rstrip("/")


# --- document update / vector reindex --------------------------------------------

@router.patch("/documents/{document_id}")
async def update_document(document_id: str, body: DocumentIn):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM documents WHERE id=?",
                           (document_id,)).fetchone():
            raise HTTPException(404, "document not found")
        con.execute("UPDATE documents SET name=?, content=? WHERE id=?",
                    (body.name, body.content, document_id))
        con.commit()
    finally:
        con.close()
    await _reindex_document(document_id)
    return {"ok": True, "embedded": _doc_chunk_count(document_id)}


@router.post("/documents/{document_id}/reindex")
async def reindex_document(document_id: str):
    con = db.connect()
    try:
        if not con.execute("SELECT 1 FROM documents WHERE id=?",
                           (document_id,)).fetchone():
            raise HTTPException(404, "document not found")
    finally:
        con.close()
    await _reindex_document(document_id)
    return {"ok": True, "chunks": _doc_chunk_count(document_id)}


def _doc_chunk_count(document_id: str) -> int:
    con = db.connect()
    try:
        return con.execute("SELECT COUNT(*) FROM doc_chunks WHERE document_id=?",
                           (document_id,)).fetchone()[0]
    finally:
        con.close()


async def _reindex_document(document_id: str) -> None:
    """Chunk + embed a document when a vector provider is configured; silently
    skip otherwise (FTS keeps working regardless)."""
    con = db.connect()
    try:
        provider_id, model = vectors.settings_keys(con)
        if not provider_id or not model:
            return
        row = con.execute("SELECT content FROM documents WHERE id=?",
                          (document_id,)).fetchone()
        if not row:
            return
        chunks = vectors.chunk_text(row["content"])
        if not chunks:
            vectors.drop_document_chunks(con, document_id)
            con.commit()
            return
        provider = load_provider(con, provider_id)
        api_key = api_key_for(con, provider_id)
    finally:
        con.close()
    vecs = await vectors.embed_texts(provider, api_key, model, chunks)
    con = db.connect()
    try:
        vectors.store_chunks(con, document_id, chunks, vecs)
        con.commit()
    finally:
        con.close()


# --- PDF / attachment text extraction ---------------------------------------------

class ExtractIn(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    data: str  # base64


@router.post("/extract")
def extract_text(body: ExtractIn):
    """Extract text from an uploaded file (currently PDF) for attachment or
    knowledge-base use. Returns extracted text; the file itself stays client-side."""
    from . import extract
    try:
        raw = base64.b64decode(body.data)
    except (ValueError, binascii.Error) as e:
        raise HTTPException(400, "invalid base64 payload") from e
    lower = body.filename.lower()
    try:
        if lower.endswith(".pdf"):
            return {"text": extract.pdf_to_text(raw), "kind": "pdf"}
        if lower.endswith((".txt", ".md", ".csv", ".json", ".log")):
            return {"text": raw.decode("utf-8", "replace")[:2_000_000], "kind": "text"}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    raise HTTPException(400, "unsupported file type")


# --- ElevenLabs TTS (optional third voice source) -----------------------------------

@router.get("/tts/elevenvoices")
async def elevenlabs_voices_endpoint():
    con = db.connect()
    try:
        key = _secret(con, "elevenlabs")
    finally:
        con.close()
    try:
        return await tts.elevenlabs_voices(key)
    except Exception as e:
        raise HTTPException(502, f"voice list unavailable: {e}") from e


@router.get("/tts/elevenlabs")
async def elevenlabs_tts_endpoint(text: str, voice: str):
    text = text.strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > tts.MAX_TTS_CHARS:
        raise HTTPException(400, f"text too long (max {tts.MAX_TTS_CHARS} chars)")
    if not re.fullmatch(r"[A-Za-z0-9]{16,40}", voice):
        raise HTTPException(400, "invalid ElevenLabs voice id")
    con = db.connect()
    try:
        key = _secret(con, "elevenlabs")
    finally:
        con.close()
    try:
        audio = await tts.elevenlabs_speak(text, voice, key)
    except Exception as e:
        raise HTTPException(502, f"speech failed: {e}") from e
    return Response(audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})
