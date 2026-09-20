"""The composition root.

Every dependency is built once here and handed to request handlers through
FastAPI's dependency system. Nothing below this module constructs a provider or
opens a database, which is what keeps the services testable: a test builds a
`Container` pointed at a temporary database and swaps in a fake engine.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Cookie, Depends, HTTPException, Request, status

from ..config import Settings, load_settings
from ..domain.models import User
from ..services.audio_service import AudioService
from ..services.curriculum_service import CurriculumService
from ..services.jobs import JobRunner
from ..services.lesson_service import LessonService
from ..services.providers import registry
from ..services.retrieval import RetrievalService
from ..storage.db import Database
from ..storage.repositories import Repositories

SESSION_COOKIE = "aloud_session"

# scrypt parameters. n=2**14 keeps sign-in responsive while making offline
# guessing expensive.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Salted scrypt digest, stored as `scrypt$<salt>$<hash>`."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification. False on any malformed stored value."""
    try:
        scheme, salt_hex, digest_hex = stored.split("$", 2)
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


@dataclass
class Container:
    """Everything the application is wired from."""

    settings: Settings
    database: Database
    repos: Repositories
    retrieval: RetrievalService
    engine: object
    tts: object
    stt: object
    curriculum: CurriculumService
    lessons: LessonService
    audio: AudioService
    jobs: JobRunner

    def provider_report(self) -> registry.ProviderReport:
        return registry.describe(self.engine, self.tts, self.stt)  # type: ignore[arg-type]

    def shutdown(self) -> None:
        self.jobs.shutdown(wait=False)


def build_container(settings: Settings | None = None) -> Container:
    """Construct the whole object graph."""
    settings = settings or load_settings()
    settings.ensure_directories()

    database = Database(settings.db_path)
    repos = Repositories(database)
    retrieval = RetrievalService()

    engine = registry.build_teaching_engine(settings)
    tts = registry.build_tts(settings)
    stt = registry.build_stt(settings)

    curriculum = CurriculumService(repos, engine, retrieval, stt)  # type: ignore[arg-type]
    lessons = LessonService(repos, engine, curriculum)          # type: ignore[arg-type]
    audio = AudioService(repos, tts, settings.audio_dir)        # type: ignore[arg-type]
    jobs = JobRunner(repos.jobs)

    return Container(
        settings=settings,
        database=database,
        repos=repos,
        retrieval=retrieval,
        engine=engine,
        tts=tts,
        stt=stt,
        curriculum=curriculum,
        lessons=lessons,
        audio=audio,
        jobs=jobs,
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def get_container(request: Request) -> Container:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - only reachable on a misconfigured app
        raise HTTPException(status_code=500, detail="The application is not ready.")
    return container


def current_user(
    container: Container = Depends(get_container),
    aloud_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    """The signed-in user, or 401.

    Authorisation is never taken from the request body or a header the client
    controls; it comes from an opaque session token looked up server-side.
    """
    user_id = container.repos.auth.user_id_for(aloud_session or "")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue."
        )
    user = container.repos.users.by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue."
        )
    return user


def optional_user(
    container: Container = Depends(get_container),
    aloud_session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User | None:
    """The signed-in user if there is one. Used by the landing page."""
    user_id = container.repos.auth.user_id_for(aloud_session or "")
    return container.repos.users.by_id(user_id) if user_id else None


def owned_space(space_id: str, user: User, container: Container):
    """Fetch a space the user owns, or 404.

    404 rather than 403 on purpose: a stranger should not be able to learn that
    a space id exists by the shape of the error.
    """
    space = container.repos.spaces.get(space_id, user.id)
    if space is None:
        raise HTTPException(status_code=404, detail="That subject could not be found.")
    return space
