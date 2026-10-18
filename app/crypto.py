"""Secret handling: Fernet key management, provider-key encryption, password
hashing, session tokens. No secret material is ever logged."""

import hashlib
import hmac
import os
import secrets
import stat
import time
from pathlib import Path

from cryptography.fernet import Fernet

SESSION_COOKIE = "ot_session"
SESSION_TTL = 30 * 24 * 3600

_fernet: Fernet | None = None
_session_key: bytes = b""


def init_crypto(data_dir: Path) -> None:
    """Load Fernet key from OPENTYPER_FERNET_KEY env or data/secret.key (0600)."""
    global _fernet, _session_key
    raw = os.environ.get("OPENTYPER_FERNET_KEY")
    if raw:
        raw = raw.strip().encode()
    else:
        key_file = data_dir / "secret.key"
        if not key_file.exists():
            key_file.write_bytes(Fernet.generate_key())
        key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        raw = key_file.read_bytes().strip()
    _fernet = Fernet(raw)
    _session_key = hashlib.sha256(raw + b"opentyper-session").digest()


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt_secret(token: bytes) -> str:
    return _fernet.decrypt(token).decode()


def key_hint(api_key: str) -> str:
    if len(api_key) <= 8:
        return "…" + api_key[-2:]
    return api_key[:3] + "…" + api_key[-4:]


# --- app password (optional) -------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                           n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(h.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


# --- session tokens (HMAC-signed expiry, keyed by the Fernet key) ------------

def _sig(payload: bytes) -> bytes:
    return hmac.new(_session_key, payload, hashlib.sha256).digest()


def make_session_token(password_hash: str) -> str:
    exp = str(int(time.time()) + SESSION_TTL)
    payload = f"{exp}:{password_hash[-16:]}".encode()
    return payload.hex() + "." + _sig(payload).hex()


def verify_session_token(token: str, password_hash: str) -> bool:
    try:
        payload_hex, sig_hex = token.split(".")
        payload = bytes.fromhex(payload_hex)
        if not hmac.compare_digest(bytes.fromhex(sig_hex), _sig(payload)):
            return False
        exp, hash_suffix = payload.decode().split(":", 1)
        return int(exp) > time.time() and hash_suffix == password_hash[-16:]
    except (ValueError, TypeError):
        return False
