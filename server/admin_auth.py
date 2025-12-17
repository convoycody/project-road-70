import os, hmac, hashlib, base64, secrets
from typing import Optional, Tuple

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")

def _b64d(s: str) -> bytes:
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("utf-8"))

def hash_password(password: str, salt_b64: Optional[str] = None, rounds: int = 180_000) -> Tuple[str, str, int]:
    salt = _b64d(salt_b64) if salt_b64 else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=32)
    return _b64e(dk), _b64e(salt), rounds

def verify_password(password: str, hash_b64: str, salt_b64: str, rounds: int) -> bool:
    dk, _, _ = hash_password(password, salt_b64=salt_b64, rounds=rounds)
    return hmac.compare_digest(dk, hash_b64)

def env_admin_user() -> str:
    return os.environ.get("ROADSTATE_ADMIN_USER", "admin")

def env_admin_hash() -> str:
    return os.environ.get("ROADSTATE_ADMIN_HASH", "")

def env_admin_salt() -> str:
    return os.environ.get("ROADSTATE_ADMIN_SALT", "")

def env_admin_rounds() -> int:
    try:
        return int(os.environ.get("ROADSTATE_ADMIN_ROUNDS", "180000"))
    except Exception:
        return 180000

def env_session_secret() -> str:
    return os.environ.get("ROADSTATE_SESSION_SECRET", "")
