"""Application factory.

Builds the object graph, mounts the API and serves the client. The client is
static files with no build step, so what runs in the browser is exactly what is
in `web/` on disk.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import routes_audio, routes_auth, routes_lessons, routes_progress, routes_spaces
from .api.deps import build_container
from .config import Settings, load_settings

logger = logging.getLogger("aloud")

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        container = build_container(settings)
        application.state.container = container
        removed = container.repos.auth.purge_expired()
        if removed:
            logger.info("Cleared %d expired sessions", removed)
        report = container.provider_report()
        logger.info("Teacher: %s", report.teacher)
        logger.info(
            "Audio files: %s | Listening: %s",
            report.downloads if report.downloads_available else "off",
            report.listening if report.listening_available else "browser only",
        )
        try:
            yield
        finally:
            container.shutdown()

    app = FastAPI(
        title="Aloud",
        description="A voice-first learning platform.",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.debug else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.debug else None,
    )

    app.include_router(routes_auth.router)
    app.include_router(routes_spaces.router)
    app.include_router(routes_lessons.router)
    app.include_router(routes_audio.router)
    app.include_router(routes_progress.router)

    _register_error_handlers(app)
    _register_security_headers(app)
    _mount_client(app)
    return app


def _looks_like_file(path: str) -> bool:
    """True when the last path segment carries an extension."""
    return "." in path.rsplit("/", 1)[-1]


def _register_error_handlers(app: FastAPI) -> None:
    """Every error leaves as JSON the client can show, never a stack trace."""

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        if exc.status_code == 404 and not path.startswith("/api/") and not _looks_like_file(path):
            # An unknown page: hand back the shell and let the client router
            # decide. A missing file stays a 404, so a broken asset reference
            # is reported as one instead of arriving as unexpected HTML.
            return FileResponse(WEB_ROOT / "index.html")
        return JSONResponse(
            {"detail": exc.detail or "Something went wrong."}, status_code=exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Turn pydantic's structure into one readable sentence.
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
        message = first.get("msg", "That input was not valid.")
        message = message.removeprefix("Value error, ")
        detail = f"{field}: {message}" if field and field not in message.lower() else message
        return JSONResponse({"detail": detail}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("Unhandled error on %s", request.url.path)
        return JSONResponse(
            {"detail": "Something went wrong at our end. Your progress is saved."},
            status_code=500,
        )


def _register_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "microphone=(self), camera=(), geolocation=()"
        )
        # No inline script or external origins: everything is served from here.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'",
        )
        return response


def _mount_client(app: FastAPI) -> None:
    if not WEB_ROOT.is_dir():  # pragma: no cover - only if the checkout is broken
        logger.warning("No web/ directory at %s", WEB_ROOT)
        return

    app.mount("/styles", StaticFiles(directory=WEB_ROOT / "styles"), name="styles")
    app.mount("/js", StaticFiles(directory=WEB_ROOT / "js"), name="js")
    assets = WEB_ROOT / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.api_route("/health", methods=["GET", "HEAD"], include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    # HEAD as well as GET: monitors and link checkers use it, and a 405 on
    # every page would look like the site is down.
    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def shell(path: str) -> FileResponse:
        """Serve the app shell for every client route.

        A request that looks like a file rather than a page is answered as a
        file or not at all. Returning the HTML shell with a 200 for a missing
        script would turn a typo into a baffling parse error in the console
        instead of a plain 404.
        """
        if _looks_like_file(path):
            candidate = (WEB_ROOT / path).resolve()
            if WEB_ROOT.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            raise StarletteHTTPException(status_code=404, detail="Not found")
        return FileResponse(WEB_ROOT / "index.html")


app = create_app()
