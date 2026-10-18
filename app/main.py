"""App assembly: startup init, auth middleware, static mount."""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import crypto, db
from .api import router
from .providers import BUILTIN
from .seeds import seed_content

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def startup() -> None:
    data_dir = Path(os.environ.get("OPENTYPER_DATA_DIR", "data"))
    db.init_db(data_dir)
    crypto.init_crypto(data_dir)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con = db.connect()
    try:
        for pid, meta in BUILTIN.items():
            con.execute(
                "INSERT INTO providers (id, name, kind, base_url, models, is_builtin, "
                "created_at, updated_at) VALUES (?,?,?,?,?,1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
                "models=excluded.models, is_builtin=1",
                (pid, meta["name"], meta["kind"], meta["base_url"],
                 json.dumps(meta["models"]), ts, ts))
        con.commit()
        seed_content(con)
    finally:
        con.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup()
    yield


app = FastAPI(title="OpenTyper", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(router)

# The session guards API routes only. The SPA shell, its assets, the share
# page, the manifest and icon are public: the shell holds no data (everything
# sensitive arrives via /api), share tokens are their own capability, and a
# locked-out browser must still receive app.js to render the sign-in modal.
PUBLIC_PREFIXES = ("/s/", "/api/public/")
PUBLIC_PATHS = ("/api/health", "/api/auth/login")


@app.get("/s/{token}")
def share_page(token: str):
    from html import escape
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Shared chat</title><link rel='stylesheet' href='/style.css'>"
        "<style>body{max-width:52rem;margin:0 auto;padding:1rem 1rem 3rem}"
        "h1{font-size:1.4rem}.msg .who{font-size:.75rem;color:#64748b;"
        "text-transform:uppercase;letter-spacing:.04em;margin:.8rem 0 .2rem}"
        ".msg .body{border-radius:10px;padding:.7rem 1rem;overflow-wrap:break-word}"
        ".msg.user .body{background:#f4f5f7}"
        ".msg.assistant .body{border:1px solid #d9dbe0}"
        "@media (prefers-color-scheme:dark){body{background:#16181d;color:#e8eaee}"
        ".msg.user .body{background:#1d2026}.msg.assistant .body{background:#1c1f25;"
        "border-color:#31363f}.msg .who{color:#8b93a1}h1{color:#e8eaee}}"
        "</style>"
        "<h1>Shared chat</h1><div id='c'></div>"
        "<script src='/vendor/marked.min.js'></script>"
        "<script src='/vendor/dompurify.min.js'></script>"
        "<script>const t=" + escape(json.dumps(token)) + ";"
        "fetch('/api/public/'+t).then(r=>{if(!r.ok)throw 0;return r.json()})"
        ".then(d=>{document.title=d.title;document.querySelector('h1').textContent=d.title;"
        "const c=document.getElementById('c');"
        "for(const m of d.messages){const el=document.createElement('div');"
        "el.className='msg '+m.role;"
        "el.innerHTML='<div class=who></div><div class=body></div>';"
        "el.querySelector('.who').textContent="
        "m.role==='user'?'You':(m.model||'Assistant');"
        "el.querySelector('.body').innerHTML="
        "DOMPurify.sanitize(marked.parse(m.content||''));"
        "c.append(el);}})"
        ".catch(()=>{document.getElementById('c').textContent='Share not found.'});"
        "</script>")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if (not path.startswith("/api") or path in PUBLIC_PATHS
            or path.startswith(PUBLIC_PREFIXES)):
        return await call_next(request)
    con = db.connect()
    try:
        row = con.execute(
            "SELECT value FROM settings WHERE key='app_password_hash'").fetchone()
    finally:
        con.close()
    if row:
        token = request.cookies.get(crypto.SESSION_COOKIE, "")
        if not crypto.verify_session_token(token, row["value"]):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
    return await call_next(request)


# SPA last so /api wins
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
