"""Password hashing and JWT issuance/verification (YGH-9, YGH-11, YGH-13)."""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-only-secret-change-me")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24h; fine for an MVP, revisit for production

# Calling bcrypt directly rather than through passlib: passlib 1.7.4 (its last
# release, unmaintained since 2020) is incompatible with bcrypt>=4.1's API and
# raises on its own internal self-test, so it was never a pinning problem to
# work around — the dependency itself was the bug.
_BCRYPT_MAX_BYTES = 72  # bcrypt silently ignores input past this; enforce it explicitly


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    password_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(password_bytes, hashed.encode("utf-8"))


def create_access_token(subject: str) -> str:
    """`subject` is the user id as a string."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Returns the user id string, or None if the token is invalid/expired."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload.get("sub")
