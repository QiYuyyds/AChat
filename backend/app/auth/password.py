"""Password hashing using bcrypt (cost factor 12)."""

import bcrypt

_COST_FACTOR = 12


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt and return the hash as a str."""
    salt = bcrypt.gensalt(rounds=_COST_FACTOR)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
