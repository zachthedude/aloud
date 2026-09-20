"""Learning spaces: subjects, their material, plan and knowledge map."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from ..domain.curriculum import knowledge_graph, recommend_next
from ..domain.mastery import overall_progress
from ..domain.models import (
    LearningSpace,
    ProcessingStatus,
    Resource,
    ResourceKind,
    SpaceStatus,
    User,
    new_id,
)
from ..services.curriculum_service import NotEnoughMaterial
from ..services.documents import pipeline
from ..services.documents.base import UnreadableDocument, UnsupportedDocument
from ..services.jobs import job_to_dict
from .deps import Container, current_user, get_container, owned_space
from .schemas import (
    NoteResourceRequest,
    SpaceRequest,
    UrlResourceRequest,
    serialise_concept,
    serialise_lesson,
    serialise_plan_item,
    serialise_recommendation,
    serialise_resource,
    serialise_space,
)

router = APIRouter(prefix="/api/spaces", tags=["spaces"])

#: How many subjects one account may hold. Generous, but not unbounded.
MAX_SPACES = 40
MAX_RESOURCES_PER_SPACE = 40

#: Upload read size. Large enough that a 300 MB video is not ten thousand
#: round trips, small enough that memory stays flat.
CHUNK_BYTES = 1024 * 1024


async def _store_streaming(chunks, *, resource_id, extension, directory, max_bytes):
    """Write an async stream to disk, enforcing the cap as it goes.

    `pipeline.store_stream` is synchronous and takes an iterable, so the async
    iteration happens here and the file work is handed to a worker thread. That
    keeps a slow disk from blocking the event loop while somebody else's lesson
    is playing.
    """
    import anyio

    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / f"{resource_id}{extension}").resolve()
    if directory.resolve() not in path.parents:
        raise pipeline.UploadRejected("That file could not be stored.")

    total = 0
    handle = await anyio.to_thread.run_sync(lambda: path.open("wb"))
    try:
        async for chunk in chunks:
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise pipeline.UploadRejected(
                    f"That file is larger than {pipeline.size_label(max_bytes)}. "
                    "Try a shorter recording, or split it up."
                )
            await anyio.to_thread.run_sync(handle.write, chunk)
    except BaseException:
        await anyio.to_thread.run_sync(handle.close)
        path.unlink(missing_ok=True)
        raise
    await anyio.to_thread.run_sync(handle.close)

    if total == 0:
        path.unlink(missing_ok=True)
        raise pipeline.UploadRejected("That file is empty.")
    return path, total


def _space_summary(container: Container, space: LearningSpace, user: User) -> dict:
    repos = container.repos
    concepts = repos.concepts.list_for_space(space.id)
    mastery = repos.mastery.for_space(user.id, space.id)
    records = [mastery[c.id] for c in concepts if c.id in mastery]
    return serialise_space(
        space,
        resource_count=len(repos.resources.list_for_space(space.id)),
        lesson_count=len(repos.lessons.list_for_space(space.id)),
        concept_count=len(concepts),
        progress=overall_progress(records) if concepts else 0.0,
        needs_review=sum(1 for r in records if r.needs_review),
    )


@router.get("")
def list_spaces(
    user: User = Depends(current_user), container: Container = Depends(get_container)
) -> dict:
    spaces = container.repos.spaces.list_for_user(user.id)
    return {"spaces": [_space_summary(container, space, user) for space in spaces]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_space(
    payload: SpaceRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    existing = container.repos.spaces.list_for_user(user.id)
    if len(existing) >= MAX_SPACES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You can have up to {MAX_SPACES} subjects. Remove one to add another.",
        )

    space = LearningSpace(
        id=new_id("spc"),
        user_id=user.id,
        title=payload.title,
        level=payload.level,
        description=payload.description,
        status=SpaceStatus.EMPTY,
        accent=len(existing) % 6,
    )
    container.repos.spaces.create(space)
    return {"space": _space_summary(container, space, user)}


@router.get("/{space_id}")
def get_space(
    space_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    space = owned_space(space_id, user, container)
    repos = container.repos
    concepts = repos.concepts.list_for_space(space_id)
    mastery = repos.mastery.for_space(user.id, space_id)
    lessons = repos.lessons.list_for_space(space_id)
    plan = container.curriculum.plan_for(space_id, user.id)

    active = repos.sessions.most_recent(user.id, unfinished_only=True)
    in_progress = active.lesson_id if active and active.space_id == space_id else ""

    job = repos.jobs.latest_for_space(space_id, "analyse")
    return {
        "space": _space_summary(container, space, user),
        "resources": [serialise_resource(r) for r in repos.resources.list_for_space(space_id)],
        "concepts": [serialise_concept(c, mastery.get(c.id)) for c in concepts],
        "lessons": [
            serialise_lesson(
                lesson,
                mastery=(mastery[lesson.concept_id].state if lesson.concept_id in mastery else None),
            )
            for lesson in lessons
        ],
        "plan": [serialise_plan_item(item) for item in plan],
        "recommendation": serialise_recommendation(
            recommend_next(plan, in_progress_lesson_id=in_progress)
        ),
        "job": job_to_dict(job) if job else None,
    }


@router.patch("/{space_id}")
def update_space(
    space_id: str,
    payload: SpaceRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    owned_space(space_id, user, container)
    container.repos.spaces.update(
        space_id, user.id,
        title=payload.title, level=payload.level, description=payload.description,
    )
    space = container.repos.spaces.get(space_id, user.id)
    return {"space": _space_summary(container, space, user)}  # type: ignore[arg-type]


@router.delete("/{space_id}")
def delete_space(
    space_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    space = owned_space(space_id, user, container)
    # Remove uploaded files as well as rows; cascade only covers the database.
    for resource in container.repos.resources.list_for_space(space.id):
        _remove_file(resource.stored_path, container)
    container.repos.spaces.delete(space_id, user.id)
    container.retrieval.invalidate(space_id)
    return {"ok": True}


@router.get("/{space_id}/map")
def knowledge_map(
    space_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    owned_space(space_id, user, container)
    concepts = container.repos.concepts.list_for_space(space_id)
    mastery = container.repos.mastery.for_space(user.id, space_id)
    lessons = {l.concept_id: l.id for l in container.repos.lessons.list_for_space(space_id)}
    graph = knowledge_graph(concepts, mastery)
    for node in graph["nodes"]:            # type: ignore[index]
        node["lessonId"] = lessons.get(node["id"], "")
    return graph


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


def _guard_resource_limit(container: Container, space_id: str) -> None:
    if len(container.repos.resources.list_for_space(space_id)) >= MAX_RESOURCES_PER_SPACE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This subject already holds {MAX_RESOURCES_PER_SPACE} resources.",
        )


@router.post("/{space_id}/resources", status_code=status.HTTP_201_CREATED)
async def upload_resource(
    space_id: str,
    file: UploadFile = File(...),
    title: str = Form(default=""),
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Accept a file, check it by signature, store it and queue analysis.

    Written as a stream rather than a `read()`. A lecture recording can run to
    hundreds of megabytes, and buffering one to measure it would let a single
    upload decide how much memory the server uses.
    """
    owned_space(space_id, user, container)
    _guard_resource_limit(container, space_id)

    try:
        extension = pipeline.check_name(file.filename or "upload")
    except pipeline.UploadRejected as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from None

    limit = (
        container.settings.max_media_bytes
        if pipeline.is_media(extension)
        else container.settings.max_upload_bytes
    )

    # The first chunk is enough to tell whether the bytes match the name, so a
    # mislabelled file is rejected before the rest of it is written to disk.
    head = await file.read(CHUNK_BYTES)
    try:
        pipeline.check_signature(extension, head[:64])
    except pipeline.UploadRejected as error:
        await file.close()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from None

    async def chunks():
        yield head
        while True:
            chunk = await file.read(CHUNK_BYTES)
            if not chunk:
                return
            yield chunk

    resource_id = new_id("res")
    try:
        stored, size = await _store_streaming(
            chunks(),
            resource_id=resource_id,
            extension=extension,
            directory=container.settings.uploads_dir / space_id,
            max_bytes=limit,
        )
    except pipeline.UploadRejected as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from None
    finally:
        await file.close()

    display = pipeline.safe_display_name(file.filename or "Untitled")
    resource = Resource(
        id=resource_id,
        space_id=space_id,
        title=(title.strip() or display.rsplit(".", 1)[0])[:120],
        kind=pipeline.kind_for(extension),
        original_filename=display,
        stored_path=str(stored),
        byte_size=size,
        status=ProcessingStatus.QUEUED,
    )
    container.repos.resources.create(resource)
    container.repos.spaces.touch(space_id)
    return {"resource": serialise_resource(resource)}


@router.post("/{space_id}/resources/url", status_code=status.HTTP_201_CREATED)
def add_url_resource(
    space_id: str,
    payload: UrlResourceRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    owned_space(space_id, user, container)
    _guard_resource_limit(container, space_id)

    try:
        document = pipeline.fetch_url(payload.url)
    except (pipeline.UploadRejected, UnreadableDocument) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from None

    resource_id = new_id("res")
    text = "\n".join(
        (f"# {block.text}" if block.is_heading else block.text) for block in document.blocks
    )
    stored = pipeline.store_upload(
        text.encode("utf-8"), resource_id=resource_id, extension=".md",
        directory=container.settings.uploads_dir / space_id,
    )
    resource = Resource(
        id=resource_id,
        space_id=space_id,
        title=document.title[:120],
        kind=ResourceKind.URL,
        source_url=payload.url,
        stored_path=str(stored),
        byte_size=len(text.encode("utf-8")),
        status=ProcessingStatus.QUEUED,
    )
    container.repos.resources.create(resource)
    container.repos.spaces.touch(space_id)
    return {"resource": serialise_resource(resource)}


@router.post("/{space_id}/resources/note", status_code=status.HTTP_201_CREATED)
def add_note_resource(
    space_id: str,
    payload: NoteResourceRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Notes typed straight in, for a student without a file to hand."""
    owned_space(space_id, user, container)
    _guard_resource_limit(container, space_id)

    resource_id = new_id("res")
    stored = pipeline.store_upload(
        payload.text.encode("utf-8"), resource_id=resource_id, extension=".md",
        directory=container.settings.uploads_dir / space_id,
    )
    resource = Resource(
        id=resource_id,
        space_id=space_id,
        title=(payload.title.strip() or "Your notes")[:120],
        kind=ResourceKind.NOTE,
        stored_path=str(stored),
        byte_size=len(payload.text.encode("utf-8")),
        status=ProcessingStatus.QUEUED,
    )
    container.repos.resources.create(resource)
    container.repos.spaces.touch(space_id)
    return {"resource": serialise_resource(resource)}


@router.delete("/{space_id}/resources/{resource_id}")
def delete_resource(
    space_id: str,
    resource_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    owned_space(space_id, user, container)
    resource = container.repos.resources.get(resource_id)
    if resource is None or resource.space_id != space_id:
        raise HTTPException(status_code=404, detail="That resource could not be found.")
    _remove_file(resource.stored_path, container)
    container.repos.resources.delete(resource_id)
    container.retrieval.invalidate(space_id)
    return {"ok": True}


def _remove_file(stored_path: str, container: Container) -> None:
    """Delete an uploaded file, refusing any path outside the upload directory."""
    if not stored_path:
        return
    try:
        path = pipeline.Path(stored_path).resolve()
        uploads = container.settings.uploads_dir.resolve()
        if uploads in path.parents:
            path.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


@router.post("/{space_id}/analyse", status_code=status.HTTP_202_ACCEPTED)
def analyse_space(
    space_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Queue the build of concepts, plan and lessons. Returns a job to watch."""
    owned_space(space_id, user, container)

    running = container.repos.jobs.latest_for_space(space_id, "analyse")
    if running and running.status in ("queued", "running") and container.jobs.is_running(running.id):
        return {"job": job_to_dict(running)}

    if not container.repos.resources.list_for_space(space_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add at least one resource before building a plan.",
        )

    def work(handle) -> None:
        try:
            container.curriculum.rebuild(space_id, user.id, handle)
        except NotEnoughMaterial as error:
            handle.fail(str(error))
        except (UnreadableDocument, UnsupportedDocument) as error:
            handle.fail(str(error))

    job = container.jobs.submit(
        "analyse", space_id=space_id, user_id=user.id, work=work
    )
    return {"job": job_to_dict(job)}


@router.get("/{space_id}/job")
def latest_job(
    space_id: str,
    kind: str = "analyse",
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    owned_space(space_id, user, container)
    job = container.repos.jobs.latest_for_space(space_id, kind)
    return {"job": job_to_dict(job) if job else None}
