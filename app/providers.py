"""Provider catalogs and streaming adapters (OpenAI-compatible, Anthropic, Google)."""

import json
from collections.abc import AsyncIterator

import httpx

TIMEOUT = httpx.Timeout(300.0, connect=60.0)

BUILTIN = {
    "openai": {
        "name": "OpenAI",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini", "o3"],
    },
    "anthropic": {
        "name": "Anthropic",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com",
        "models": ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-1"],
    },
    "google": {
        "name": "Google",
        "kind": "google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash"],
    },
    "openrouter": {
        "name": "OpenRouter",
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["openrouter/auto"],
    },
    # all OpenAI-compatible; model ids drift — editable via Settings → Providers
    "groq": {
        "name": "Groq",
        "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"],
    },
    "deepseek": {
        "name": "DeepSeek",
        "kind": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "mistral": {
        "name": "Mistral",
        "kind": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "models": ["mistral-large-latest", "mistral-small-latest", "codestral-latest"],
    },
    "xai": {
        "name": "xAI",
        "kind": "openai",
        "base_url": "https://api.x.ai/v1",
        "models": ["grok-4", "grok-3", "grok-3-mini"],
    },
    "perplexity": {
        "name": "Perplexity",
        "kind": "openai",
        "base_url": "https://api.perplexity.ai",
        "models": ["sonar", "sonar-pro", "sonar-reasoning"],
    },
    "huggingface": {
        "name": "Hugging Face",
        "kind": "openai",
        "base_url": "https://router.huggingface.co/v1",
        "models": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct"],
    },
}


# Per-model-family starting parameters, applied at chat creation when the
# user (or agent/folder) didn't set values explicitly. First prefix match wins.
# temperature None = don't send the parameter (required for reasoning models
# like o3 / gpt-5 / deepseek-reasoner, which reject non-default temperatures).
RECOMMENDED_PARAMS = [
    ("o3", {"temperature": None}),
    ("gpt-5", {"temperature": None}),
    ("deepseek-reasoner", {"temperature": None}),
    ("sonar", {"temperature": 0.2}),          # search-grounded: low randomness
    ("codestral", {"temperature": 0.2}),      # code: deterministic
    ("claude", {"temperature": 1.0}),         # Anthropic's tuned default
    ("deepseek-chat", {"temperature": 0.7}),
    ("gemini", {"temperature": 0.7}),
    ("grok", {"temperature": 0.7}),
    ("llama", {"temperature": 0.7}),
    ("gpt-4o", {"temperature": 0.7}),
    ("mistral", {"temperature": 0.7}),
]
DEFAULT_TEMPERATURE = 0.7

# Reasoning-effort mapping per dialect. "none" disables thinking where the
# dialect allows it; everything else maps to a thinking budget (Anthropic/
# Google take token budgets, OpenAI takes a named level).
ANTHROPIC_THINKING_BUDGET = {"low": 2048, "medium": 8192, "high": 16384,
                             "xhigh": 32768, "max": 63999}
GOOGLE_THINKING_BUDGET = {"none": 0, "low": 1024, "medium": 8192, "high": 16384,
                          "xhigh": 24576, "max": 32768}
OPENAI_EFFORT = {"low": "low", "medium": "medium", "high": "high",
                 "xhigh": "high", "max": "high"}


def recommended_params(model: str) -> dict:
    model = (model or "").lower()
    for prefix, params in RECOMMENDED_PARAMS:
        if model.startswith(prefix):
            return params
    return {"temperature": DEFAULT_TEMPERATURE}


class ProviderError(Exception):
    """Upstream failure with a scrubbed, user-presentable message."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _scrub(text: str, api_key: str | None) -> str:
    if api_key and api_key in text:
        text = text.replace(api_key, "***")
    return text


def _base(provider: dict) -> str:
    return (provider.get("base_url") or "").rstrip("/")


async def _sse_objects(resp: httpx.Response) -> AsyncIterator[dict]:
    async for line in resp.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            if data == "[DONE]":
                return
            continue
        yield json.loads(data)


def _headers(provider: dict, api_key: str) -> dict:
    if provider["kind"] == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"}
    if provider["kind"] == "google":
        return {"x-goog-api-key": api_key, "content-type": "application/json"}
    return {"Authorization": f"Bearer {api_key}", "content-type": "application/json"}


def _content(m: dict, kind: str):
    """Message content, attaching images when the dialect supports them.
    m["images"] is a list of (mime, base64) tuples. Google always gets a list
    of part objects — its API rejects a bare string in `parts`."""
    images = m.get("images") or []
    if kind == "google":
        return [{"text": m["content"]}] + [
            {"inlineData": {"mimeType": mime, "data": b64}} for mime, b64 in images]
    if not images:
        return m["content"]
    text = {"type": "text", "text": m["content"]}
    if kind == "anthropic":
        return [text] + [{"type": "image",
                          "source": {"type": "base64", "media_type": mime, "data": b64}}
                         for mime, b64 in images]
    return [text] + [{"type": "image_url",
                      "image_url": {"url": f"data:{mime};base64,{b64}"}}
                     for mime, b64 in images]


def _body(provider: dict, model: str, messages: list[dict], params: dict,
          stream: bool) -> tuple[str, dict]:
    """Return (url, json_body). messages: [{role, content, images?}] with optional
    leading system prompt already split out for anthropic/google."""
    kind = provider["kind"]
    system = next((m["content"] for m in messages if m["role"] == "system"), None)
    chat = [m for m in messages if m["role"] != "system"]
    temperature, max_tokens, top_p = (params.get("temperature"),
                                      params.get("max_tokens"), params.get("top_p"))
    freq, pres = params.get("frequency_penalty"), params.get("presence_penalty")

    effort = params.get("reasoning_effort")
    cache = bool(params.get("cache_prompt"))

    if kind == "anthropic":
        body: dict = {"model": model, "stream": stream,
                      "max_tokens": max_tokens or 4096,
                      "messages": [{"role": m["role"], "content": _content(m, kind)}
                                   for m in chat]}
        budget = ANTHROPIC_THINKING_BUDGET.get(effort or "")
        if budget:
            # thinking requires default temperature and max_tokens above the budget
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["max_tokens"] = max(body["max_tokens"], budget + 1024)
        else:
            if temperature is not None:
                body["temperature"] = temperature
            if top_p is not None:
                body["top_p"] = top_p
        if system:
            if cache:  # cache the (stable) system prefix across calls
                body["system"] = [{"type": "text", "text": system,
                                   "cache_control": {"type": "ephemeral"}}]
            else:
                body["system"] = system
        if cache and body["messages"]:
            # mark the final content block so the whole conversation prefix is
            # eligible for reuse on the next call
            last = body["messages"][-1]
            content = last["content"]
            block = ({"type": "text", "text": content, "cache_control":
                      {"type": "ephemeral"}} if isinstance(content, str) else content)
            if isinstance(block, list) and block:
                block[-1] = {**block[-1], "cache_control": {"type": "ephemeral"}}
            elif isinstance(block, dict):
                block["cache_control"] = {"type": "ephemeral"}
            last["content"] = block if not isinstance(content, str) else [block]
        return _base(provider) + "/v1/messages", body

    if kind == "google":
        contents = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": _content(m, kind)} for m in chat]
        body = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        gen: dict = {}
        if temperature is not None:
            gen["temperature"] = temperature
        if max_tokens:
            gen["maxOutputTokens"] = max_tokens
        if top_p is not None:
            gen["topP"] = top_p
        if effort and effort in GOOGLE_THINKING_BUDGET:
            gen["thinkingConfig"] = {"thinkingBudget": GOOGLE_THINKING_BUDGET[effort]}
        if gen:
            body["generationConfig"] = gen
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{_base(provider)}/models/{model}:{method}", body

    body = {"model": model, "stream": stream,
            "messages": [{"role": m["role"], "content": _content(m, kind)}
                         for m in messages]}
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens:
        body["max_tokens"] = max_tokens
    if top_p is not None:
        body["top_p"] = top_p
    if freq is not None:
        body["frequency_penalty"] = freq
    if pres is not None:
        body["presence_penalty"] = pres
    if effort and effort in OPENAI_EFFORT:
        body["reasoning_effort"] = OPENAI_EFFORT[effort]
    # OpenAI/Gemini prompt caching is server-side and automatic; only Anthropic
    # takes an explicit request. Cached-token reads are reported in usage.
    if provider.get("id") == "openai" and stream:  # usage in the final chunk
        body["stream_options"] = {"include_usage": True}
    return _base(provider) + "/chat/completions", body


async def _raise_for_status(resp: httpx.Response, api_key: str) -> None:
    if resp.status_code < 400:
        return
    detail = (await resp.aread()).decode(errors="replace")[:500]
    hint = " (check API key)" if resp.status_code in (401, 403) else ""
    raise ProviderError(f"provider returned {resp.status_code}{hint}: "
                        f"{_scrub(detail, api_key)}", resp.status_code)


async def stream_chat(provider: dict, api_key: str, model: str,
                      messages: list[dict], params: dict,
                      client: httpx.AsyncClient,
                      usage: dict | None = None) -> AsyncIterator[str]:
    """Yield text deltas; when `usage` dict is given, fill token counts seen
    in stream metadata (best-effort — callers fall back to an estimate)."""
    url, body = _body(provider, model, messages, params, stream=True)
    async with client.stream("POST", url, json=body,
                             headers=_headers(provider, api_key), timeout=TIMEOUT) as resp:
        await _raise_for_status(resp, api_key)
        kind = provider["kind"]
        async for obj in _sse_objects(resp):
            if usage is not None:
                if kind == "anthropic":
                    if obj.get("type") == "message_start":
                        u = obj.get("message", {}).get("usage", {})
                        usage["prompt_tokens"] = u.get("input_tokens")
                        if u.get("cache_read_input_tokens") is not None:
                            usage["cache_read_tokens"] = u["cache_read_input_tokens"]
                        if u.get("cache_creation_input_tokens") is not None:
                            usage["cache_write_tokens"] = u["cache_creation_input_tokens"]
                    elif obj.get("type") == "message_delta":
                        usage["completion_tokens"] = obj.get("usage", {}).get("output_tokens")
                elif kind == "google":
                    u = obj.get("usageMetadata", {})
                    if u:
                        usage["prompt_tokens"] = u.get("promptTokenCount")
                        usage["completion_tokens"] = u.get("candidatesTokenCount")
                        if u.get("cachedContentTokenCount") is not None:
                            usage["cache_read_tokens"] = u["cachedContentTokenCount"]
                elif obj.get("usage"):
                    u = obj["usage"]
                    usage["prompt_tokens"] = u.get("prompt_tokens")
                    usage["completion_tokens"] = u.get("completion_tokens")
                    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
                    if cached is not None:
                        usage["cache_read_tokens"] = cached
            if kind == "anthropic":
                if obj.get("type") == "content_block_delta":
                    text = obj.get("delta", {}).get("text")
                    if text:
                        yield text
            elif kind == "google":
                for part in obj.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                    if part.get("text"):
                        yield part["text"]
            else:
                delta = obj.get("choices", [{}])[0].get("delta", {}) \
                    if obj.get("choices") else {}
                if delta.get("content"):
                    yield delta["content"]


async def list_remote_models(provider: dict, api_key: str,
                             client: httpx.AsyncClient) -> list[str]:
    """Fetch the live model list from the provider's /models endpoint."""
    kind = provider["kind"]
    url = _base(provider) + ("/models" if kind != "anthropic" else "/v1/models")
    resp = await client.get(url, headers=_headers(provider, api_key),
                            timeout=httpx.Timeout(30.0, connect=10.0))
    if resp.status_code >= 400:
        hint = " (check API key)" if resp.status_code in (401, 403) else ""
        raise ProviderError(f"listing models failed: {resp.status_code}{hint}: "
                            f"{_scrub(resp.text[:300], api_key)}", resp.status_code)
    data = resp.json()
    if kind == "anthropic":
        return sorted(m["id"] for m in data.get("data", []) if m.get("id"))
    if kind == "google":
        out = []
        for m in data.get("models", []):
            name = m.get("name", "")
            mid = name.split("/")[-1] if "/" in name else name
            if "generateContent" in m.get("supportedGenerationMethods", []) \
                    and "embedding" not in mid and mid:
                out.append(mid)
        return sorted(out)
    return sorted(m.get("id") for m in data.get("data", []) if m.get("id"))


async def test_provider(provider: dict, api_key: str, model: str,
                        client: httpx.AsyncClient) -> str:
    """Minimal completion to prove the key + endpoint + model actually work.
    Params left empty so provider defaults apply (o3 etc. reject max_tokens)."""
    text, _usage = await complete(provider, api_key, model, [
        {"role": "user", "content": "Reply with exactly: ok"}], {}, client)
    return text


async def complete(provider: dict, api_key: str, model: str,
                   messages: list[dict], params: dict,
                   client: httpx.AsyncClient) -> tuple[str, dict | None]:
    """One-shot completion. Returns (text, usage) — usage is None when the
    provider does not report token counts."""
    url, body = _body(provider, model, messages, params, stream=False)
    resp = await client.post(url, json=body, headers=_headers(provider, api_key),
                             timeout=TIMEOUT)
    if resp.status_code >= 400:
        hint = " (check API key)" if resp.status_code in (401, 403) else ""
        raise ProviderError(f"provider returned {resp.status_code}{hint}: "
                            f"{_scrub(resp.text[:500], api_key)}", resp.status_code)
    data = resp.json()
    kind = provider["kind"]
    if kind == "anthropic":
        text = "".join(c.get("text", "") for c in data.get("content", []))
        u = data.get("usage", {})
        usage = ({"prompt_tokens": u.get("input_tokens"),
                  "completion_tokens": u.get("output_tokens")} if u else None)
        if usage and u.get("cache_read_input_tokens") is not None:
            usage["cache_read_tokens"] = u["cache_read_input_tokens"]
        return text, usage
    if kind == "google":
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        u = data.get("usageMetadata", {})
        usage = ({"prompt_tokens": u.get("promptTokenCount"),
                  "completion_tokens": u.get("candidatesTokenCount")} if u else None)
        if usage and u.get("cachedContentTokenCount") is not None:
            usage["cache_read_tokens"] = u["cachedContentTokenCount"]
        return "".join(p.get("text", "") for p in parts), usage
    u = data.get("usage", {})
    usage = ({"prompt_tokens": u.get("prompt_tokens"),
              "completion_tokens": u.get("completion_tokens")} if u else None)
    if usage and (u.get("prompt_tokens_details") or {}).get("cached_tokens") is not None:
        usage["cache_read_tokens"] = u["prompt_tokens_details"]["cached_tokens"]
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content, usage
