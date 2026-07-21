"""Secret-at-rest encryption for connection passwords (PRD FR-3, NFR-4) and
password hashing for local user accounts. No external auth dependency
(passlib/bcrypt) — stdlib hashlib.pbkdf2_hmac is enough for this scope and
avoids a native-extension install risk.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

_KEY_PATH = Path(__file__).resolve().parent.parent / "data" / "secret.key"


def _load_or_create_key() -> bytes:
    env_key = os.environ.get("LIDVALID_SECRET_KEY")
    if env_key:
        return env_key.encode() if isinstance(env_key, str) else env_key
    if os.environ.get("LIDVALID_ENV", "development") == "production":
        raise RuntimeError(
            "LIDVALID_SECRET_KEY must be set when LIDVALID_ENV=production -- refusing "
            "to fall back to data/secret.key, which would silently break decryption of "
            "every already-stored connection secret and invalidate all sessions on the "
            "next redeploy. Generate one with: python -c \"from cryptography.fernet "
            "import Fernet; print(Fernet.generate_key().decode())\""
        )
    _KEY_PATH.parent.mkdir(exist_ok=True)
    if _KEY_PATH.exists():
        return _KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    _KEY_PATH.write_bytes(key)
    return key


_fernet = Fernet(_load_or_create_key())


def encrypt_secret(plaintext: str) -> bytes:
    if not plaintext:
        return b""
    return _fernet.encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes | None) -> str:
    if not ciphertext:
        return ""
    return _fernet.decrypt(ciphertext).decode("utf-8")


# ---- password hashing (PBKDF2-HMAC-SHA256, stdlib only) ----

_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest_hex = stored.split("$")
        iterations = int(iterations)
    except (ValueError, AttributeError):
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(check.hex(), digest_hex)
