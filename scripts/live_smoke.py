#!/usr/bin/env python3
"""Live-provider smoke test: boots the app in a throwaway data dir, imports
keys from .env (same mapping as the Settings → Providers import), and sends
one short message per provider through the real SSE stream.

Defaults to the free-tier trio (google, groq, openrouter). Paid providers are
only tested when named explicitly:

    uv run scripts/live_smoke.py                     # google groq openrouter
    uv run scripts/live_smoke.py openai google       # add paid ones deliberately

Exit 0 = every requested provider streamed a reply. Keys are never printed;
provider errors are shown as the app scrubs them."""

import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENV_KEYS = {
    "OPENAI_API_KEY": "openai", "ANTHROPIC_API_KEY": "anthropic",
    "GOOGLE_API_KEY": "google", "GEMINI_API_KEY": "google",
    "OPENROUTER_API_KEY": "openrouter", "GROQ_API_KEY": "groq",
    "DEEPSEEK_API_KEY": "deepseek", "MISTRAL_API_KEY": "mistral",
    "XAI_API_KEY": "xai", "PERPLEXITY_API_KEY": "perplexity",
}
DEFAULT_PROVIDERS = ["google", "groq", "openrouter"]
# catalog ids drift; try these in order, first success wins
MODEL_LADDER = {
    "google": ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"],
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"],
    "openrouter": ["openrouter/auto", "nvidia/nemotron-3.5-lightning:free",
                   "cohere/north-mini-code:free"],
    "openai": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o"],
    "deepseek": ["deepseek-chat"],
    "anthropic": ["claude-haiku-4-5"],
}
PROMPT = "Reply with exactly: SMOKE OK"


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def parse_env(path: Path) -> dict[str, list[str]]:
    """provider id -> candidate keys (active lines first, commented as fallback)."""
    found: dict[str, list[str]] = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^\s*#?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$", line)
        if not m:
            continue
        pid = ENV_KEYS.get(m.group(1))
        val = m.group(2).strip("'\"")
        if not pid or not val or len(val) < 8:
            continue
        found.setdefault(pid, [])
        if line.lstrip().startswith("#"):       # commented: fallback candidate
            found[pid].append(val)
        else:
            found[pid].insert(0, val)
    return found


def serve(app, port: int, lifespan="on") -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="critical", lifespan=lifespan))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def smoke_provider(c: httpx.Client, pid: str, keys: list[str]) -> tuple[bool, str]:
    """Try each .env candidate key; per key, walk the model ladder. Returns (ok, detail)."""
    last_err = ""
    for key in keys:
        r = c.put(f"/api/providers/{pid}/key", json={"api_key": key})
        if r.status_code != 200:
            return False, f"key storage failed: {r.status_code}"
        prov = next(p for p in c.get("/api/providers").json() if p["id"] == pid)
        if key in json.dumps(prov):
            return False, "RAW KEY LEAKED in /api/providers response"
        for model in MODEL_LADDER.get(pid, []):
            cid = c.post("/api/chats", json={"provider_id": pid, "model": model}).json()["id"]
            frames, t0 = [], time.time()
            try:
                with c.stream("POST", f"/api/chats/{cid}/messages",
                              json={"content": PROMPT}, timeout=90) as resp:
                    if resp.status_code != 200:
                        last_err = f"{model}: HTTP {resp.status_code} " + resp.read()[:120].decode("utf-8", "replace")
                        continue
                    for line in resp.iter_lines():
                        if line.startswith("data:") or line.startswith("event:"):
                            frames.append(line.split(":", 1)[1].strip())
            except httpx.HTTPError as e:
                last_err = f"{model}: {e}"
                continue
            done = any(f == "[DONE]" or "done" in f for f in frames)
            detail = c.get(f"/api/chats/{cid}").json()
            assistant = next((m for m in detail["messages"]
                              if m["role"] == "assistant"), None)
            if not assistant or not assistant["content"]:
                last_err = f"{model}: no assistant content ({last_err})"
                continue
            usage = assistant.get("usage") or assistant
            toks = f"{usage.get('prompt_tokens', '?')}+{usage.get('completion_tokens', '?')} tok"
            est = "~" if usage.get("estimated") else ""
            title = f' · auto-title "{detail["title"][:30]}"' if detail["title"] else ""
            return True, (f"{model} · {time.time() - t0:.1f}s · {est}{toks} · done={done}"
                          f"{title} · reply: {assistant['content'][:48]!r}")
        if "401" in last_err or "403" in last_err:
            continue  # auth rejected: try the next .env candidate
        break       # reachable but models failed: key is fine, stop here
    return False, last_err or "all catalog models failed"


def main() -> int:
    wanted = sys.argv[1:] or DEFAULT_PROVIDERS
    env = parse_env(ROOT / ".env")
    missing = [p for p in wanted if not env.get(p)]
    if missing:
        print(f"no key in .env for: {', '.join(missing)}")
        return 1

    data_dir = Path(tempfile.mkdtemp(prefix="opentyper-live-"))
    os.environ["OPENTYPER_DATA_DIR"] = str(data_dir)
    os.chdir(ROOT)
    from app.main import app as ot_app

    port = free_port()
    srv = serve(ot_app, port)
    base = f"http://127.0.0.1:{port}"
    c = httpx.Client(base_url=base, timeout=120)
    for _ in range(100):
        try:
            if c.get("/api/health", timeout=0.2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)

    print(f"live smoke · providers: {', '.join(wanted)}\n")
    failed = 0
    for pid in wanted:
        ok, detail = smoke_provider(c, pid, env[pid])
        print(f"  [{'PASS' if ok else 'FAIL'}] {pid}: {detail}")
        failed += 0 if ok else 1

    srv.should_exit = True
    print(f"\ndata dir (throwaway): {data_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
