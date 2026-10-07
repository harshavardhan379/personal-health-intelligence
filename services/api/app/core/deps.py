"""Shared FastAPI dependencies: DB session and the authenticated user (YGH-13, YGH-14)."""

import uuid
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from packages.schemas.db import get_db
from packages.schemas.models import User
from services.api.app.core.security import decode_access_token

# HTTPBearer (not OAuth2PasswordBearer) is the correct scheme for what we actually
# built: a custom JWT login returning a bearer token, not the OAuth2 password grant
# (which expects a form-encoded username/password at tokenUrl). Using the wrong one
# made Swagger's "Authorize" button show a fake login form that doesn't match our
# real /auth/login (JSON body, field is "email" not "username") and errored.
# HTTPBearer instead shows one plain "paste your token" field in /docs.
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Every protected route depends on this. No route should query the DB with a
    user id taken from anywhere except this function's return value — that is what
    makes cross-user access impossible by construction rather than by convention.
    """
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise credentials_error

    user_id_str = decode_access_token(credentials.credentials)
    if user_id_str is None:
        raise credentials_error

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise credentials_error from None

    user = db.get(User, user_id)
    if user is None:
        raise credentials_error
    return user
