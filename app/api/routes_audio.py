"""Audio files: preparing them, streaming them, downloading them.

Every path here is checked against the signed-in user before a byte is served.
Audio lives outside the web root and is streamed by the application, so a
stored path is never a URL anyone can guess.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse

from ..domain.models import User
from ..services.jobs import job_to_dict
from .deps import Container, current_user, get_container
from .schemas import AudioRequest

router = APIRouter(prefix="/api/audio", tags=["audio"])


def _voice_for(user: User, container: Container, requested: str = "") -> str:
    return requested or user.preferred_voice or container.audio.default_voice()


def _owned_lesson(container: Container, user: User, lesson_id: str):
    lesson = container.repos.lessons.get(lesson_id)
    if lesson is None or container.repos.spaces.get(lesson.space_id, user.id) is None:
        raise HTTPException(status_code=404, detail="That lesson could not be found.")
    return lesson


@router.get("/status/{lesson_id}")
def status_for_lesson(
    lesson_id: str,
    voice: str = "",
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    _owned_lesson(container, user, lesson_id)
    return container.audio.lesson_status(lesson_id, _voice_for(user, container, voice))


@router.post("/prepare/{lesson_id}", status_code=status.HTTP_202_ACCEPTED)
def prepare_lesson_audio(
    lesson_id: str,
    payload: AudioRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Render a lesson to audio files in the background."""
    lesson = _owned_lesson(container, user, lesson_id)
    if not container.audio.available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Saving lessons as files is switched off on this server.",
        )

    voice = _voice_for(user, container, payload.voice)
    existing = container.repos.jobs.latest_for_space(lesson.space_id, "audio")
    if existing and existing.status in ("queued", "running") and container.jobs.is_running(existing.id):
        return {"job": job_to_dict(existing)}

    def work(handle) -> None:
        container.audio.prepare_lesson(lesson, voice=voice, handle=handle)

    job = container.jobs.submit(
        "audio", space_id=lesson.space_id, user_id=user.id, work=work,
        detail={"lessonId": lesson_id, "voice": voice},
    )
    return {"job": job_to_dict(job)}


@router.get("/section/{section_id}")
def stream_section(
    section_id: str,
    voice: str = "",
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> FileResponse:
    """Stream one rendered section."""
    section = container.repos.lessons.section(section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="That audio could not be found.")
    _owned_lesson(container, user, section.lesson_id)

    asset = container.audio.section_asset(section_id, _voice_for(user, container, voice))
    if asset is None:
        raise HTTPException(status_code=404, detail="That audio has not been prepared yet.")

    path = Path(asset.file_path)
    if not _inside_audio_dir(path, container):
        raise HTTPException(status_code=404, detail="That audio could not be found.")

    return FileResponse(
        path,
        media_type=asset.mime_type,
        headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
    )


@router.get("/download/{lesson_id}")
def download_lesson(
    lesson_id: str,
    voice: str = "",
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> Response:
    """The whole lesson as one file to keep."""
    _owned_lesson(container, user, lesson_id)
    result = container.audio.lesson_file(lesson_id, _voice_for(user, container, voice))
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="This lesson has not been saved as audio yet.",
        )
    data, mime, filename = result
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(data)),
        },
    )


def _inside_audio_dir(path: Path, container: Container) -> bool:
    """Never serve a file from outside the audio directory."""
    try:
        resolved = path.resolve()
        return container.settings.audio_dir.resolve() in resolved.parents and resolved.is_file()
    except OSError:
        return False
