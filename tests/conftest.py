"""Test fixtures.

The API tests drive a real uvicorn server over real HTTP rather than an
in-process test client. That is deliberate twice over: it exercises the
middleware, cookie handling and error handlers a client would actually meet,
and it avoids depending on a modern `httpx`, which cannot be installed on this
machine without breaking another package that pins an old one.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.storage.db import Database  # noqa: E402
from app.storage.repositories import Repositories  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """An isolated configuration: temp database, temp uploads, no providers."""
    data_dir = tmp_path / "data"
    value = Settings(
        host="127.0.0.1",
        port=0,
        debug=True,
        secret_key="test-secret",
        data_dir=data_dir,
        database_url=f"sqlite:///{data_dir / 'test.db'}",
        max_upload_mb=2,
        llm_provider="ondevice",
        tts_provider="none",
        stt_provider="none",
    )
    value.ensure_directories()
    return value


@pytest.fixture
def repos(settings: Settings) -> Repositories:
    return Repositories(Database(settings.db_path))


@pytest.fixture
def container(settings: Settings):
    from app.api.deps import build_container

    built = build_container(settings)
    yield built
    built.shutdown()


# ---------------------------------------------------------------------------
# A live server, and a tiny client for it
# ---------------------------------------------------------------------------


class Headers(dict):
    """HTTP header names are case-insensitive; ASGI servers send them lowercase."""

    def get(self, key, default=None):  # type: ignore[override]
        for name, value in self.items():
            if name.lower() == key.lower():
                return value
        return default

    def __contains__(self, key) -> bool:  # type: ignore[override]
        return self.get(key) is not None


class Client:
    """Minimal HTTP client that keeps cookies, like a browser would."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )

    def request(self, method: str, path: str, body=None, *, raw: bool = False):
        data = None
        headers = {}
        if isinstance(body, bytes):
            data = body
        elif body is not None:
            data = json.dumps(body).encode()
            headers["content-type"] = "application/json"

        request = urllib.request.Request(
            f"{self.base}{path}", data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                payload = response.read()
                status = response.status
                response_headers = Headers(response.headers)
        except urllib.error.HTTPError as error:
            payload = error.read()
            status = error.code
            response_headers = Headers(error.headers)

        if raw:
            return status, payload, response_headers
        try:
            return status, json.loads(payload) if payload else None
        except json.JSONDecodeError:
            return status, payload.decode("utf-8", "replace")

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body, **kw)

    def patch(self, path, body=None, **kw):
        return self.request("PATCH", path, body, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    def upload(self, path: str, filename: str, content: bytes, field: str = "file"):
        """A multipart upload, hand-built to avoid a request library."""
        boundary = "----aloudtestboundary"
        parts = [
            f"--{boundary}".encode(),
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'.encode(),
            b"Content-Type: application/octet-stream",
            b"",
            content,
            f"--{boundary}--".encode(),
            b"",
        ]
        body = b"\r\n".join(parts)
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, json.loads(payload) if payload else None
            except json.JSONDecodeError:
                return error.code, payload.decode("utf-8", "replace")

    def wait_for_job(self, space_id: str, *, timeout: float = 60.0) -> dict:
        """Poll an analysis job to completion."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            status, payload = self.get(f"/api/spaces/{space_id}/job")
            job = payload.get("job") if payload else None
            if job and job["status"] in ("done", "failed"):
                return job
            time.sleep(0.25)
        raise AssertionError("Job did not finish in time")


@pytest.fixture(scope="function")
def server(settings: Settings):
    """Boot the app on a free port for the duration of one test."""
    import uvicorn

    from app.main import create_app

    port = free_port()
    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    instance = uvicorn.Server(config)

    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()

    deadline = time.time() + 25
    while time.time() < deadline and not instance.started:
        time.sleep(0.05)
    if not instance.started:
        raise RuntimeError("Server did not start")

    yield f"http://127.0.0.1:{port}"

    instance.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def client(server: str) -> Client:
    return Client(server)


@pytest.fixture
def signed_in(client: Client) -> Client:
    status, payload = client.post(
        "/api/auth/signup",
        {"email": "test@aloud.app", "password": "correcthorse", "displayName": "Test"},
    )
    assert status == 201, payload
    return client


# ---------------------------------------------------------------------------
# Sample material
# ---------------------------------------------------------------------------

SAMPLE_MARKDOWN = """# Waves

## Wavelength
Wavelength is the distance between two consecutive peaks of a wave. Wavelength is
measured in metres and is written with the symbol lambda. A longer wavelength means
a lower frequency for the same wave speed.

## Frequency
Frequency is the number of complete waves passing a point each second. Frequency is
measured in hertz. Doubling the frequency halves the wavelength when the speed stays
the same. A common misconception is that frequency and amplitude are the same thing.

## Wave Speed
The speed of a wave is calculated as v = f x lambda, where v is the wave speed, f is
the frequency and lambda is the wavelength. For example, a wave with a frequency of 5
hertz and a wavelength of 2 metres travels at 10 metres per second. Wave speed depends
on the medium the wave travels through, not on the frequency.

## Amplitude
Amplitude is the maximum displacement of a point on a wave from its rest position.
Amplitude determines how much energy a wave carries. A larger amplitude means more
energy. Amplitude does not affect the speed of the wave.
"""


@pytest.fixture
def sample_markdown() -> bytes:
    return SAMPLE_MARKDOWN.encode("utf-8")
