"""Environment-driven configuration.

Every setting has a working default so the application boots with no .env file
at all. Secrets are read from the environment and never written to disk.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ProviderChoice = Literal["auto", "ondevice", "anthropic"]

_TRUE = {"1", "true", "yes", "on"}


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key)
    return raw.lower() in _TRUE if raw else default


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader. Existing environment variables always win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    """Immutable application settings resolved once at startup."""

    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False
    secret_key: str = ""

    data_dir: Path = field(default_factory=lambda: Path("./data"))
    database_url: str = "sqlite:///./data/aloud.db"
    max_upload_mb: int = 25
    # Recordings get their own, larger cap. An hour of lecture video routinely
    # runs to a few hundred megabytes, and holding documents to the same limit
    # would either block real uploads or wave through a 300 MB PDF.
    max_media_mb: int = 300

    llm_provider: str = "auto"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    tts_provider: str = "auto"
    stt_provider: str = "auto"
    whisper_model: str = "base"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_media_bytes(self) -> int:
        return self.max_media_mb * 1024 * 1024

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def db_path(self) -> Path:
        """Filesystem path behind `database_url` for the sqlite:// scheme."""
        url = self.database_url
        if url.startswith("sqlite:///"):
            return Path(url[len("sqlite:///") :])
        raise ValueError(
            f"Unsupported ALOUD_DATABASE_URL {url!r}. "
            "Only sqlite:///path URLs are supported in this build."
        )

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.uploads_dir, self.audio_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


def load_settings(env_file: Path | None = None) -> Settings:
    """Resolve settings from the environment, loading `.env` first if present."""
    root = Path(__file__).resolve().parent.parent
    _load_dotenv(env_file or root / ".env")

    data_dir = Path(_env("ALOUD_DATA_DIR", "./data")).expanduser()
    if not data_dir.is_absolute():
        data_dir = (root / data_dir).resolve()

    database_url = _env("ALOUD_DATABASE_URL") or f"sqlite:///{data_dir / 'aloud.db'}"

    secret_key = _env("ALOUD_SECRET_KEY") or secrets.token_hex(32)

    return Settings(
        host=_env("ALOUD_HOST", "127.0.0.1"),
        port=_env_int("ALOUD_PORT", 8000),
        debug=_env_bool("ALOUD_DEBUG", False),
        secret_key=secret_key,
        data_dir=data_dir,
        database_url=database_url,
        max_upload_mb=_env_int("ALOUD_MAX_UPLOAD_MB", 25),
        max_media_mb=_env_int("ALOUD_MAX_MEDIA_MB", 300),
        llm_provider=_env("ALOUD_LLM_PROVIDER", "auto").lower(),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ALOUD_ANTHROPIC_MODEL", "claude-sonnet-5"),
        tts_provider=_env("ALOUD_TTS_PROVIDER", "auto").lower(),
        stt_provider=_env("ALOUD_STT_PROVIDER", "auto").lower(),
        whisper_model=_env("ALOUD_WHISPER_MODEL", "base"),
    )
