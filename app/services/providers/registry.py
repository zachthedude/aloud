"""Provider wiring.

The one place that decides which concrete provider serves each interface.
Everything else receives what it needs through the container in
`app/api/deps.py` and never asks which vendor is behind it.

Selection is explicit and reported honestly: `describe()` powers the settings
screen, so a student can see what is teaching them and whether downloads work,
instead of guessing why a button is disabled.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...config import Settings
from .anthropic import AnthropicProvider
from .base import SpeechToTextProvider, TeachingEngine, TextToSpeechProvider
from .model_engine import ModelBackedEngine
from .ondevice import OnDeviceEngine
from .speech import GoogleTTS, NullSTT, NullTTS, SystemTTS, WhisperSTT


@dataclass(frozen=True)
class ProviderReport:
    """What is actually running, for the settings screen."""

    teacher: str
    teacher_note: str
    grounded_only: bool
    downloads: str
    downloads_available: bool
    listening: str
    listening_available: bool


def build_teaching_engine(settings: Settings) -> TeachingEngine:
    on_device = OnDeviceEngine()
    choice = settings.llm_provider

    if choice == "ondevice":
        return on_device

    if choice in ("auto", "anthropic") and settings.anthropic_api_key:
        provider = AnthropicProvider(settings.anthropic_api_key, settings.anthropic_model)
        return ModelBackedEngine(provider, fallback=on_device)

    if choice == "anthropic":
        # Asked for explicitly and not configured: fail loudly rather than
        # silently teaching with something the operator did not choose.
        raise RuntimeError(
            "ALOUD_LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set. "
            "Set the key, or use ALOUD_LLM_PROVIDER=auto."
        )

    return on_device


def build_tts(settings: Settings) -> TextToSpeechProvider:
    choice = settings.tts_provider

    if choice == "none":
        return NullTTS()
    if choice == "gtts":
        provider = GoogleTTS()
        return provider if provider.available else NullTTS()
    if choice == "pyttsx3":
        provider = SystemTTS()
        return provider if provider.available else NullTTS()

    # auto: prefer MP3 over network, fall back to the offline system voice.
    google = GoogleTTS()
    if google.available:
        return google
    system = SystemTTS()
    if system.available:
        return system
    return NullTTS()


def build_stt(settings: Settings) -> SpeechToTextProvider:
    choice = settings.stt_provider
    if choice == "none":
        return NullSTT()
    whisper = WhisperSTT(settings.whisper_model)
    if choice == "whisper":
        return whisper if whisper.available else NullSTT()
    return whisper if whisper.available else NullSTT()


def describe(
    engine: TeachingEngine, tts: TextToSpeechProvider, stt: SpeechToTextProvider
) -> ProviderReport:
    grounded = getattr(engine, "grounded_only", True)
    return ProviderReport(
        teacher=engine.display_name,
        teacher_note=(
            "Teaching from your uploaded material only. Add an API key for freer "
            "explanation and richer analogies."
            if grounded
            else "Teaching from your uploaded material, with wider knowledge where it helps."
        ),
        grounded_only=grounded,
        downloads=tts.display_name,
        downloads_available=tts.available,
        listening=stt.display_name,
        listening_available=stt.available,
    )
