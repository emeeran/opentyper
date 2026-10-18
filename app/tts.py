"""Text-to-speech via Microsoft Edge's public read-aloud voices.

Server-side because browsers can't reach the service directly (WebSocket,
no CORS). No API key involved — this is the same unauthenticated endpoint
the Edge browser uses for Read Aloud.
"""

import httpx

import edge_tts

MAX_TTS_CHARS = 5000

ELEVENLABS_URL = "https://api.elevenlabs.io/v1"

_voices_cache: list[dict] | None = None
_el_voices_cache: list[dict] | None = None


async def edge_voices() -> list[dict]:
    """Neural voice catalog; cached in-process (the list changes rarely)."""
    global _voices_cache
    if _voices_cache is None:
        manager = await edge_tts.VoicesManager.create()
        _voices_cache = [
            {"id": v["ShortName"], "name": v["FriendlyName"],
             "lang": v["Locale"], "gender": v["Gender"]}
            for v in manager.voices
        ]
    return _voices_cache


async def edge_speak(text: str, voice: str) -> bytes:
    """Synthesize text to MP3 bytes. Raises on service/network failure."""
    communicate = edge_tts.Communicate(text[:MAX_TTS_CHARS], voice)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf += chunk["data"]
    if not buf:
        raise RuntimeError("service returned no audio")
    return bytes(buf)


# --- ElevenLabs (optional third voice source; key stored encrypted) -----------

async def elevenlabs_voices(api_key: str) -> list[dict]:
    global _el_voices_cache
    if _el_voices_cache is None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{ELEVENLABS_URL}/voices",
                                    headers={"xi-api-key": api_key})
        if resp.status_code >= 400:
            raise RuntimeError(f"voice list failed: {resp.status_code}")
        _el_voices_cache = [
            {"id": v["voice_id"], "name": v["name"],
             "lang": (v.get("labels") or {}).get("language", "")}
            for v in resp.json().get("voices", [])]
    return _el_voices_cache


async def elevenlabs_speak(text: str, voice_id: str, api_key: str) -> bytes:
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{ELEVENLABS_URL}/text-to-speech/{voice_id}",
            json={"text": text[:MAX_TTS_CHARS], "model_id": "eleven_multilingual_v2"},
            headers={"xi-api-key": api_key, "accept": "audio/mpeg"})
    if resp.status_code >= 400:
        raise RuntimeError(f"speech failed: {resp.status_code}: {resp.text[:200]}")
    return resp.content
