"""Headless browser QA.

Drives Chrome over the DevTools Protocol so a check can do what a person
would: sign in, open every screen at several widths, capture what it looks
like, and collect the console errors and failed requests that a screenshot
alone would hide.

    python tools/qa.py --base http://127.0.0.1:8111

Exits non-zero when any page logs a console error or fails a request, so it
can gate a change rather than just produce pictures.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

try:
    from websockets.sync.client import connect
except ImportError:  # pragma: no cover - dependency ships with uvicorn[standard]
    print("websockets is required: python -m pip install websockets", file=sys.stderr)
    raise SystemExit(2)

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

#: Console noise that is expected and not a defect.
IGNORED = (
    "favicon",
    "speechSynthesis",
    "not-allowed",
    "The AudioContext was not allowed to start",
    "Autoplay",
)


def find_chrome() -> str:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("chrome") or shutil.which("chromium") or shutil.which("google-chrome")
    if found:
        return found
    raise SystemExit("No Chrome or Edge binary was found.")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class PageReport:
    name: str
    url: str
    width: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    screenshot: str = ""
    text_sample: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors and not self.failed_requests


class Browser:
    """A very small CDP client: enough to drive one tab."""

    def __init__(self, chrome: str, *, headless: bool = True) -> None:
        self.port = free_port()
        self.profile = Path(tempfile.mkdtemp(prefix="aloud-qa-"))
        args = [
            chrome,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--mute-audio",
            "--hide-scrollbars",
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
        ]
        if headless:
            args.append("--headless=new")
        self.process = subprocess.Popen(
            args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.ws = None
        self._id = 0
        self._connect()

    def _connect(self, timeout: float = 25.0) -> None:
        deadline = time.time() + timeout
        url = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=2
                ) as response:
                    targets = json.loads(response.read())
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:  # noqa: BLE001 - chrome is still booting
                time.sleep(0.3)
        if not url:
            raise SystemExit("Could not attach to Chrome.")
        # No keepalive pings: a long screenshot can outlast the default
        # ping timeout, and the library then closes the connection
        # mid-sweep. Chrome is local, so a dead peer is not a worry.
        self.ws = connect(
            url,
            max_size=96 * 1024 * 1024,
            open_timeout=20,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
        )

    def send(self, method: str, params: dict | None = None, *, timeout: float = 30.0) -> dict:
        self._id += 1
        message_id = self._id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv(timeout=max(0.1, deadline - time.time()))
            message = json.loads(raw)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self._buffer.append(message)
        raise TimeoutError(method)

    _buffer: list[dict] = []

    def drain(self, seconds: float) -> list[dict]:
        """Collect events for a while."""
        events = list(self._buffer)
        self._buffer.clear()
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = self.ws.recv(timeout=max(0.05, deadline - time.time()))
            except TimeoutError:
                break
            except Exception:  # noqa: BLE001 - socket closed between checks
                break
            events.append(json.loads(raw))
        return events

    def close(self) -> None:
        try:
            if self.ws:
                self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


def sign_in(base: str, email: str, password: str) -> str:
    """Sign in over HTTP and return the session cookie value."""
    request = urllib.request.Request(
        f"{base}/api/auth/signin",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        cookies = response.headers.get_all("Set-Cookie") or []
    for cookie in cookies:
        if cookie.startswith("aloud_session="):
            return cookie.split("=", 1)[1].split(";", 1)[0]
    raise SystemExit("Sign-in did not return a session cookie.")


THEME = [""]


def visit(
    browser: Browser,
    name: str,
    url: str,
    *,
    width: int,
    height: int,
    out_dir: Path,
    settle: float = 2.2,
    script: str = "",
) -> PageReport:
    report = PageReport(name=name, url=url, width=width)

    browser.send("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height,
        "deviceScaleFactor": 1,
        "mobile": width < 700,
    })
    browser.send("Page.navigate", {"url": url})
    events = browser.drain(settle)

    if script:
        try:
            browser.send("Runtime.evaluate", {"expression": script, "awaitPromise": True})
            events += browser.drain(1.6)
        except Exception as error:  # noqa: BLE001
            report.errors.append(f"script failed: {error}")

    for event in events:
        method = event.get("method")
        params = event.get("params", {})

        if method == "Runtime.consoleAPICalled":
            level = params.get("type")
            text = " ".join(
                str(arg.get("value", arg.get("description", "")))
                for arg in params.get("args", [])
            ).strip()
            if not text or any(skip in text for skip in IGNORED):
                continue
            if level == "error":
                report.errors.append(text[:300])
            elif level == "warning":
                report.warnings.append(text[:200])

        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails", {})
            text = details.get("exception", {}).get("description") or details.get("text", "")
            if text and not any(skip in text for skip in IGNORED):
                report.errors.append(str(text)[:300])

        elif method == "Network.loadingFailed":
            error_text = params.get("errorText", "")
            if "ERR_ABORTED" in error_text:
                continue
            report.failed_requests.append(f"{params.get('type')}: {error_text}")

        elif method == "Network.responseReceived":
            status = params.get("response", {}).get("status", 0)
            request_url = params.get("response", {}).get("url", "")
            if status >= 400 and "favicon" not in request_url:
                report.failed_requests.append(f"{status} {request_url.split('?')[0][-70:]}")

    # A sample of the rendered text, so a blank page is detectable.
    try:
        result = browser.send("Runtime.evaluate", {
            "expression": "document.body.innerText.replace(/\\s+/g,' ').trim().slice(0,400)",
            "returnByValue": True,
        })
        report.text_sample = result.get("result", {}).get("value", "") or ""
    except Exception:  # noqa: BLE001
        pass

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"-{THEME[0]}" if THEME[0] else ""
    filename = out_dir / f"{name}-{width}{suffix}.png"

    # A full-height capture of a page with a position:fixed overlay composites
    # the overlay over the scrolled page behind it, which is not what anyone
    # sees. Capture just the viewport when an overlay is present.
    overlay = False
    try:
        result = browser.send("Runtime.evaluate", {
            "expression": "Boolean(document.querySelector('.player, .modal-backdrop'))",
            "returnByValue": True,
        })
        overlay = bool(result.get("result", {}).get("value"))
    except Exception:  # noqa: BLE001
        pass

    try:
        shot = browser.send(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": not overlay},
            timeout=45,
        )
        filename.write_bytes(base64.b64decode(shot["data"]))
        report.screenshot = str(filename)
    except Exception as error:  # noqa: BLE001
        report.errors.append(f"screenshot failed: {error}")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless QA sweep.")
    parser.add_argument("--base", default="http://127.0.0.1:8111")
    parser.add_argument("--out", default="tools/screenshots")
    parser.add_argument("--email", default="student@aloud.app")
    parser.add_argument("--password", default="learnaloud")
    parser.add_argument("--only", default="", help="Comma-separated page names")
    parser.add_argument("--widths", default="1440,390")
    parser.add_argument("--theme", default="", help="light or dark; blank leaves it alone")
    args = parser.parse_args()

    base = args.base.rstrip("/")
    out_dir = Path(args.out)
    widths = [int(w) for w in args.widths.split(",") if w.strip()]

    THEME[0] = args.theme
    token = sign_in(base, args.email, args.password)

    # Find real ids so the deep pages are exercised, not just the shells.
    opener = urllib.request.build_opener()
    opener.addheaders = [("Cookie", f"aloud_session={token}")]

    def get(path: str):
        with opener.open(f"{base}{path}", timeout=15) as response:
            return json.loads(response.read())

    spaces = get("/api/spaces")["spaces"]
    space_id = spaces[0]["id"] if spaces else ""
    detail = get(f"/api/spaces/{space_id}") if space_id else {"lessons": []}
    lesson_id = detail["lessons"][0]["id"] if detail.get("lessons") else ""

    pages = [
        ("landing", "/", False),
        ("signin", "/signin", False),
        ("signup", "/signup", False),
        ("welcome", "/welcome", True),
        ("dashboard", "/app", True),
        ("subjects", "/spaces", True),
        ("subject-new", "/spaces/new", True),
        ("subject-plan", f"/space/{space_id}?view=plan", True),
        ("subject-material", f"/space/{space_id}?view=material", True),
        ("subject-map", f"/space/{space_id}?view=map", True),
        ("room-quarantine", f"/room/{space_id}/quarantine", True),
        ("room-last90", f"/room/{space_id}/last90", True),
        ("plan", "/plan", True),
        ("modes", "/modes", True),
        ("library", "/library", True),
        ("progress", "/progress", True),
        ("settings", "/settings", True),
    ]
    if args.only:
        wanted = {name.strip() for name in args.only.split(",")}
        pages = [p for p in pages if p[0] in wanted]

    chrome = find_chrome()
    browser = Browser(chrome)
    reports: list[PageReport] = []

    try:
        browser.send("Page.enable")
        browser.send("Runtime.enable")
        browser.send("Network.enable")
        browser.send("Log.enable")
        browser.send("Network.setCookie", {
            "name": "aloud_session",
            "value": token,
            "domain": "127.0.0.1",
            "path": "/",
        })
        if args.theme:
            # The app resolves the theme from localStorage before first paint,
            # so seed it there rather than trying to click the toggle.
            browser.send("Page.addScriptToEvaluateOnNewDocument", {
                "source": f"try{{localStorage.setItem('aloud-theme','{args.theme}')}}catch(e){{}}"
            })

        for width in widths:
            height = 900 if width > 700 else 844
            for name, path, _needs_auth in pages:
                reports.append(
                    visit(browser, name, f"{base}{path}", width=width, height=height, out_dir=out_dir)
                )

            # The player is a screen in its own right, reached by opening a lesson.
            if lesson_id:
                reports.append(
                    visit(
                        browser, "player", f"{base}/app",
                        width=width, height=height, out_dir=out_dir, settle=3.2,
                        script=(
                            "(async () => {"
                            "  const m = await import('/js/core/lessonplayer.js');"
                            f"  await m.lessonPlayer.open('{lesson_id}');"
                            "  await new Promise(r => setTimeout(r, 900));"
                            "})()"
                        ),
                    )
                )
                reports.append(
                    visit(
                        browser, "player-question", f"{base}/app",
                        width=width, height=height, out_dir=out_dir, settle=3.2,
                        script=(
                            "(async () => {"
                            "  const m = await import('/js/core/lessonplayer.js');"
                            "  const s = await import('/js/core/store.js');"
                            f"  await m.lessonPlayer.open('{lesson_id}');"
                            "  await new Promise(r => setTimeout(r, 700));"
                            "  const st = s.store.get().player;"
                            "  const idx = st.sections.findIndex(x => x.question);"
                            "  if (idx >= 0) {"
                            "    s.setPlayer({ sectionIndex: idx, question: st.sections[idx].question,"
                            "                  state: 'waiting_for_student' });"
                            "  }"
                            "  await new Promise(r => setTimeout(r, 500));"
                            "})()"
                        ),
                    )
                )
    finally:
        browser.close()

    # Report
    width_label = {}
    failures = 0
    print(f"\n{'PAGE':<22} {'W':>5}  {'ERR':>4} {'WARN':>5} {'NET':>4}  TEXT")
    print("-" * 96)
    for report in reports:
        status = "OK " if report.ok else "FAIL"
        sample = report.text_sample[:44].replace("\n", " ")
        print(
            f"{report.name:<22} {report.width:>5}  "
            f"{len(report.errors):>4} {len(report.warnings):>5} {len(report.failed_requests):>4}  "
            f"{status} {sample}"
        )
        if not report.ok:
            failures += 1
            for message in report.errors:
                print(f"    ERROR  {message}")
            for message in report.failed_requests:
                print(f"    NET    {message}")
        if not report.text_sample.strip():
            print("    BLANK  no visible text")

    print(f"\nScreenshots in {out_dir.resolve()}")
    print(f"{len(reports) - failures}/{len(reports)} clean")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
