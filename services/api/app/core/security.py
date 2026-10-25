"""Password hashing and JWT issuance/verification (YGH-9, YGH-11, YGH-13)."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from services.api.app.core.config import settings

# The secret and the lifetime come from settings (JWT_SECRET, JWT_EXPIRE_MINUTES); production
# refuses to start on the development default (Settings.production_problems).
JWT_ALGORITHM = "HS256"

# Calling bcrypt directly rather than through passlib: passlib 1.7.4 (its last
# release, unmaintained since 2020) is incompatible with bcrypt>=4.1's API and
# raises on its own internal self-test, so it was never a pinning problem to
# work around — the dependency itself was the bug.
_BCRYPT_MAX_BYTES = 72  # bcrypt silently ignores input past this; enforce it explicitly
MAX_PASSWORD_BYTES = _BCRYPT_MAX_BYTES
MIN_PASSWORD_CHARS = 8


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    password_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(password_bytes, hashed.encode("utf-8"))


def create_access_token(subject: str) -> str:
    """`subject` is the user id as a string."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """Returns the user id string, or None if the token is invalid/expired."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload.get("sub")
