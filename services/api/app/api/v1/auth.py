"""Registration, login and the current-user endpoint (YGH-9, YGH-11)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from packages.schemas.db import get_db
from packages.schemas.models import User
from services.api.app.core.deps import get_current_user
from services.api.app.core.ratelimit import (
    client_address,
    count_and_refuse_if_limited,
    login_failures,
    refuse_if_limited,
    register_attempts,
)
from services.api.app.core.security import (
    MAX_PASSWORD_BYTES,
    MIN_PASSWORD_CHARS,
    create_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: str
    email: str

    model_config = {"from_attributes": True}


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    count_and_refuse_if_limited(register_attempts, client_address(request))
    if len(payload.password) < MIN_PASSWORD_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at least {MIN_PASSWORD_CHARS} characters")
    if len(payload.password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        # bcrypt reads only the first 72 bytes: longer passwords would silently share a hash with
        # their own first 72 bytes, so they are refused rather than quietly truncated.
        raise HTTPException(
            status_code=422,
            detail=f"Password must be at most {MAX_PASSWORD_BYTES} bytes long")

    existing = db.query(User).filter_by(email=payload.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    attempt = f"{client_address(request)}|{payload.email.lower()}"
    refuse_if_limited(login_failures, attempt)  # before any password work, so it costs nothing
    user = db.query(User).filter_by(email=payload.email).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        login_failures.record(attempt)
        # Same message for "no such user" and "wrong password" — don't help an
        # attacker enumerate registered emails.
        raise HTTPException(status_code=401, detail="Invalid email or password")
    login_failures.reset(attempt)

    return TokenResponse(access_token=create_access_token(str(user.id)))


@router.post("/logout")
def logout():
    # Tokens are stateless JWTs with no server-side session for this MVP, so logout
    # is enforced client-side (discard the token). Revisit with a token blacklist
    # or short-lived tokens + refresh if that becomes a real requirement.
    return {"status": "ok"}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserResponse(id=str(current_user.id), email=current_user.email)
