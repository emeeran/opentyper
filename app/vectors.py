"""Optional embedding-backed retrieval over the documents knowledge base.

Documents are chunked and embedded through any OpenAI-compatible /embeddings
endpoint (configurable in Settings). Vectors are stored as JSON in SQLite —
no native extensions. Retrieval is hybrid: vector cosine top-k merged with the
existing FTS keyword snippets. When no embedding provider is configured,
knowledge injection falls back to FTS-only exactly as before.
"""

import json
import math

import httpx

from . import providers as prov

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
EMBED_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


def settings_keys(con) -> tuple[str | None, str | None]:
    """(embedding_provider_id, embedding_model) from the settings table."""

    def get(key: str) -> str | None:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    return get("embedding_provider_id"), get("embedding_model")


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        piece = text[start:start + size]
        if len(piece) < size:
            chunks.append(piece)
            break
        # prefer breaking on a paragraph/sentence boundary near the end
        cut = max(piece.rfind("\n\n", size // 2), piece.rfind(". ", size // 2))
        if cut > 0:
            piece = piece[:cut + 1]
        chunks.append(piece.strip())
        start += max(len(piece) - overlap, size - overlap)
    return [c for c in chunks if c]


async def embed_texts(provider: dict, api_key: str, model: str,
                      texts: list[str]) -> list[list[float]]:
    """Embed via the provider's OpenAI-compatible /embeddings endpoint."""
    url = (provider.get("base_url") or "").rstrip("/") + "/embeddings"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json={"model": model, "input": texts},
                                 headers=prov._headers(provider, api_key),
                                 timeout=EMBED_TIMEOUT)
    if resp.status_code >= 400:
        raise prov.ProviderError(
            f"embedding request failed: {resp.status_code}: {resp.text[:200]}",
            resp.status_code)
    data = sorted(resp.json().get("data", []), key=lambda d: d.get("index", 0))
    vecs = [d.get("embedding") for d in data]
    if len(vecs) != len(texts) or any(not v for v in vecs):
        raise prov.ProviderError("embedding response did not cover all inputs")
    return vecs


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def retrieve(con, query_vec: list[float], k: int = 3) -> list[str]:
    """Top-k document chunks by cosine similarity, formatted like FTS snippets."""
    rows = con.execute(
        "SELECT c.content, c.vec, d.name FROM doc_chunks c "
        "JOIN documents d ON d.id = c.document_id").fetchall()
    scored = []
    for r in rows:
        try:
            score = _cosine(query_vec, json.loads(r["vec"]))
        except (ValueError, TypeError):
            continue
        scored.append((score, r["name"], r["content"]))
    scored.sort(key=lambda t: -t[0])
    return [f"[{name}] {content[:400]}" for score, name, content in scored[:k]
            if score > 0.15]


def drop_document_chunks(con, document_id: str) -> None:
    con.execute("DELETE FROM doc_chunks WHERE document_id=?", (document_id,))


def store_chunks(con, document_id: str, chunks: list[str],
                 vecs: list[list[float]]) -> None:
    from .api import new_id  # local import to avoid a cycle at module load
    drop_document_chunks(con, document_id)
    for i, (chunk, vec) in enumerate(zip(chunks, vecs)):
        con.execute("INSERT INTO doc_chunks (id, document_id, idx, content, vec) "
                    "VALUES (?,?,?,?,?)",
                    (new_id(), document_id, i, chunk, json.dumps(vec)))
