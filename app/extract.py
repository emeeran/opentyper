"""Text extraction for attachments: PDF via pypdf (optional dependency).

Everything is best-effort — callers show a friendly error when pypdf is not
installed or a file is encrypted/empty instead of failing the request.
"""

MAX_EXTRACT_BYTES = 20 * 1024 * 1024


def pdf_to_text(raw: bytes) -> str:
    """Extract text from PDF bytes. Raises ValueError with a user-facing
    message when extraction is impossible."""
    try:
        import pypdf
    except ImportError as e:  # pragma: no cover - depends on install
        raise ValueError("PDF support needs the pypdf package (uv add pypdf)") from e
    if len(raw) > MAX_EXTRACT_BYTES:
        raise ValueError("file too large (max 20 MB)")
    import io
    try:
        reader = pypdf.PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("PDF is password-protected")
        pages = [(p.extract_text() or "") for p in reader.pages]
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"could not read PDF: {e}") from e
    text = "\n\n".join(t for t in pages if t).strip()
    if not text:
        raise ValueError("no extractable text (scanned/image PDF?)")
    return text[:2_000_000]
