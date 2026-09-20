"""Background work.

Analysing a textbook and rendering audio both take longer than a request should
last. They run on a small worker pool, write progress to the `jobs` table, and
the client polls one endpoint to follow along.

Progress messages are written for a student to read: "Reading your material",
not "Chunking 47 blocks". The steps are named in `Step` so the wording lives in
one place rather than being scattered through the pipeline.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from ..domain.models import Job, new_id, utcnow
from ..storage.repositories import JobRepository

logger = logging.getLogger("aloud.jobs")


class Step:
    """Student-facing progress wording."""

    QUEUED = "Getting ready"
    READING = "Reading your material"
    UNDERSTANDING = "Working out what this covers"
    PLANNING = "Building your learning plan"
    WRITING = "Preparing your lessons"
    AUDIO = "Preparing audio"
    DONE = "Ready to learn"
    FAILED = "Something went wrong"


class JobRunner:
    """A small thread pool with progress reporting.

    Deliberately in-process. One student's material is a handful of seconds of
    work, and an external queue would be infrastructure without a job to do at
    this size. `submit` is the only coupling point, so moving to Celery or RQ
    later means reimplementing this class, not rewriting callers.
    """

    def __init__(self, jobs: JobRepository, *, workers: int = 2) -> None:
        self._jobs = jobs
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aloud-job")
        self._running: dict[str, Future[Any]] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        kind: str,
        *,
        space_id: str,
        user_id: str,
        work: Callable[["JobHandle"], None],
        detail: dict[str, Any] | None = None,
    ) -> Job:
        """Queue work and return the job to watch immediately."""
        job = Job(
            id=new_id("job"),
            space_id=space_id,
            user_id=user_id,
            kind=kind,
            status="queued",
            step=Step.QUEUED,
            detail=detail or {},
        )
        self._jobs.create(job)
        handle = JobHandle(job.id, self._jobs)
        future = self._pool.submit(self._run, handle, work)
        with self._lock:
            self._running[job.id] = future
        return job

    def _run(self, handle: "JobHandle", work: Callable[["JobHandle"], None]) -> None:
        handle.start()
        try:
            work(handle)
        except Exception as error:  # noqa: BLE001 - a job must never take the server down
            logger.exception("Job %s failed", handle.job_id)
            handle.fail(str(error))
        finally:
            with self._lock:
                self._running.pop(handle.job_id, None)

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            future = self._running.get(job_id)
        return bool(future and not future.done())

    def shutdown(self, *, wait: bool = False) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)


class JobHandle:
    """What a running job uses to report progress."""

    def __init__(self, job_id: str, jobs: JobRepository) -> None:
        self.job_id = job_id
        self._jobs = jobs
        self._detail: dict[str, Any] = {}

    def start(self) -> None:
        self._jobs.update(self.job_id, status="running", step=Step.QUEUED, progress=0.02)

    def step(self, step: str, progress: float) -> None:
        self._jobs.update(self.job_id, step=step, progress=progress)

    def detail(self, **values: Any) -> None:
        """Attach structured results the client can use when the job finishes."""
        self._detail.update(values)
        self._jobs.update(self.job_id, detail=self._detail)

    def done(self, step: str = Step.DONE, **values: Any) -> None:
        self._detail.update(values)
        self._jobs.update(
            self.job_id, status="done", step=step, progress=1.0, detail=self._detail
        )

    def fail(self, message: str) -> None:
        self._jobs.update(
            self.job_id,
            status="failed",
            step=Step.FAILED,
            error=message[:400],
            detail=self._detail,
        )


def job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "step": job.step,
        "progress": round(job.progress, 3),
        "detail": job.detail,
        "error": job.error,
        "updatedAt": (job.updated_at or utcnow()).isoformat(),
    }
