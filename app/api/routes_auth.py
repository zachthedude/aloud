"""Accounts and sessions."""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from ..domain.models import User, new_id
from ..services.modes import catalogue as mode_catalogue
from ..services.modes.registry import effective_flags
from .deps import (
    SESSION_COOKIE,
    Container,
    current_user,
    get_container,
    hash_password,
    new_session_token,
    verify_password,
)
from .schemas import PreferencesRequest, SignInRequest, SignUpRequest, serialise_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: 30 days. Long enough that a student on a phone is not signed out mid-course.
SESSION_DAYS = 30


def _set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,          # not readable from JavaScript
        samesite="lax",         # survives normal navigation, blocks cross-site POSTs
        secure=secure,          # HTTPS only, when not on localhost
        path="/",
    )


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def sign_up(
    payload: SignUpRequest,
    response: Response,
    container: Container = Depends(get_container),
) -> dict:
    email = payload.email.lower().strip()
    if container.repos.users.by_email(email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for that email. Sign in instead.",
        )

    user = User(
        id=new_id("usr"),
        email=email,
        display_name=(payload.display_name or email.split("@")[0]).strip()[:80],
        password_hash=hash_password(payload.password),
    )
    container.repos.users.create(user)

    token = new_session_token()
    container.repos.auth.create(user.id, token, lifetime_days=SESSION_DAYS)
    _set_session_cookie(response, token, secure=not container.settings.debug)
    return {"user": serialise_user(user)}


@router.post("/signin")
def sign_in(
    payload: SignInRequest,
    response: Response,
    container: Container = Depends(get_container),
) -> dict:
    user = container.repos.users.by_email(payload.email)
    # Verify even when the user is unknown, so timing does not reveal which
    # emails have accounts.
    stored = user.password_hash if user else "scrypt$00$00"
    if not verify_password(payload.password, stored) or user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That email and password do not match.",
        )

    token = new_session_token()
    container.repos.auth.create(user.id, token, lifetime_days=SESSION_DAYS)
    _set_session_cookie(response, token, secure=not container.settings.debug)
    return {"user": serialise_user(user)}


@router.post("/signout")
def sign_out(
    response: Response,
    container: Container = Depends(get_container),
    aloud_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    if aloud_session:
        container.repos.auth.revoke(aloud_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    report = container.provider_report()
    return {
        "user": serialise_user(user),
        "features": effective_flags(container.settings.features),
        "providers": {
            "teacher": report.teacher,
            "teacherNote": report.teacher_note,
            "groundedOnly": report.grounded_only,
            "downloads": report.downloads,
            "downloadsAvailable": report.downloads_available,
            "listening": report.listening,
            "listeningAvailable": report.listening_available,
            "voices": container.tts.voices() if report.downloads_available else [],
        },
    }


@router.get("/modes")
def modes(
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Every crunch mode, including the ones not built yet.

    Computed here rather than in the client so there is one answer to "can I
    use this", and the interface can never offer a mode the server refuses.
    """
    has_key = bool(container.settings.anthropic_api_key)
    return {
        "modes": mode_catalogue(
            flags=effective_flags(container.settings.features),
            has_model=has_key,
            has_vision=has_key,
            muted=set(user.muted_modes),
        )
    }


@router.patch("/me")
def update_me(
    payload: PreferencesRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    container.repos.users.update_preferences(
        user.id,
        display_name=payload.display_name,
        preferred_speed=payload.preferred_speed,
        preferred_voice=payload.preferred_voice,
        explanation_style=payload.explanation_style,
        onboarded=payload.onboarded,
        muted_modes=payload.muted_modes,
    )
    updated = container.repos.users.by_id(user.id)
    return {"user": serialise_user(updated)} if updated else {"user": serialise_user(user)}
