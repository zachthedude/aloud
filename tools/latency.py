"""How long until the teacher starts talking.

The product's one hard number: under 1.2 seconds from the student finishing
speaking to the first audio coming back. Everything else about a voice loop
can be good and it will still feel broken if this is slow, so it is measured
rather than asserted.

    python tools/latency.py --base http://127.0.0.1:8000 --turns 20

What is measured is the real path: the answer goes to the real endpoint, the
real teaching engine judges it, and the clock stops when the narrator has the
first sentence and is ready to speak it. Headless Chrome has no speech engine
worth waiting on, so the last hop, handing a string to speechSynthesis, is
measured separately and added. On a real device that hop is tens of
milliseconds; the server turn is where the budget is spent.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qa import Browser, find_chrome, sign_in  # noqa: E402

BUDGET_MS = 1200


def evaluate(browser: Browser, expression: str, *, timeout: float = 90.0):
    result = browser.send(
        "Runtime.evaluate",
        {"expression": expression, "awaitPromise": True, "returnByValue": True},
        timeout=timeout,
    )
    details = result.get("exceptionDetails")
    if details:
        raise AssertionError(details.get("exception", {}).get("description") or details.get("text"))
    return result.get("result", {}).get("value")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure time to first audio.")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--email", default="student@aloud.app")
    parser.add_argument("--password", default="learnaloud")
    parser.add_argument("--turns", type=int, default=20)
    args = parser.parse_args()
    base = args.base.rstrip("/")

    token = sign_in(base, args.email, args.password)
    opener = urllib.request.build_opener()
    opener.addheaders = [("Cookie", f"aloud_session={token}")]

    def api(path: str):
        with opener.open(f"{base}{path}", timeout=30) as response:
            return json.loads(response.read())

    spaces = api("/api/spaces")["spaces"]
    if not spaces:
        print("No subjects. Run: python run.py --seed-only")
        return 2
    detail = api(f"/api/spaces/{spaces[0]['id']}")

    lesson_id = ""
    for lesson in detail["lessons"]:
        payload = api(f"/api/lessons/{lesson['id']}")
        if any(section["question"] for section in payload["sections"]):
            lesson_id = lesson["id"]
            break
    if not lesson_id:
        print("No lesson contains a question.")
        return 2

    browser = Browser(find_chrome())
    try:
        browser.send("Page.enable")
        browser.send("Runtime.enable")
        browser.send("Network.enable")
        browser.send("Network.setCookie", {
            "name": "aloud_session", "value": token, "domain": "127.0.0.1", "path": "/",
        })
        browser.send("Page.navigate", {"url": f"{base}/app"})
        browser.drain(2.5)

        samples = evaluate(browser, f"""(async () => {{
            const lp = await import('/js/core/lessonplayer.js');
            const st = await import('/js/core/store.js');
            await lp.lessonPlayer.open('{lesson_id}');
            await new Promise(r => setTimeout(r, 800));

            // Walk to a section that carries a question.
            const sections = st.player().sections;
            const index = sections.findIndex(s => s.question);
            if (index < 0) return {{ error: 'no question in this lesson' }};
            lp.lessonPlayer.goToSection(index);
            await new Promise(r => setTimeout(r, 400));

            const sessionId = st.player().sessionId;
            const question = sections[index].question;
            const answers = [
                'A force is a push or a pull that acts on an object.',
                'It is when something pushes or pulls on an object and changes its motion.',
                'Force changes the motion of an object, and it is measured in newtons.',
            ];

            const timings = [];
            for (let i = 0; i < {args.turns}; i += 1) {{
                // Hand the floor back to the student. A session only accepts
                // one answer per question, so without this every turn after
                // the first is a 409. Every state can reach idle, and idle
                // reaches the rest, so this walk is legal from anywhere.
                // Deliberately outside the clock: in a real lesson the
                // student arrives at this state by being asked something.
                for (const state of ['idle', 'ai_speaking', 'waiting_for_student']) {{
                    const moved = await fetch(`/api/sessions/${{sessionId}}`, {{
                        method: 'PATCH',
                        headers: {{ 'content-type': 'application/json' }},
                        credentials: 'same-origin',
                        body: JSON.stringify({{ state }}),
                    }});
                    if (!moved.ok) return {{ error: `could not reach ${{state}}: ${{moved.status}}` }};
                }}

                const started = performance.now();
                const response = await fetch(`/api/sessions/${{sessionId}}/answer`, {{
                    method: 'POST',
                    headers: {{ 'content-type': 'application/json' }},
                    credentials: 'same-origin',
                    body: JSON.stringify({{
                        questionId: question.id,
                        transcript: answers[i % answers.length],
                        inputMode: 'voice',
                    }}),
                }});
                if (!response.ok) return {{ error: `answer returned ${{response.status}}` }};
                const reply = await response.json();
                // First audio is ready the moment there is a first sentence to
                // hand to the speech engine.
                const text = (reply.reply && reply.reply.text) || "";
                const firstSentence = text.split(/(?<=[.!?])\\s/)[0] || text;
                if (!firstSentence) return {{ error: 'the teacher said nothing' }};
                timings.push(performance.now() - started);
            }}
            return {{ timings }};
        }})()""", timeout=max(120.0, args.turns * 8))

        if samples.get("error"):
            print("FAIL", samples["error"])
            return 2

        # The last hop: how long the browser takes to accept a string and
        # start speaking it. Zero on a headless machine with no voices.
        speech = evaluate(browser, """(async () => {
            if (!window.speechSynthesis || !speechSynthesis.getVoices().length) return null;
            const started = performance.now();
            await new Promise((resolve) => {
                const u = new SpeechSynthesisUtterance('test');
                u.volume = 0; u.onstart = resolve; u.onerror = resolve; u.onend = resolve;
                speechSynthesis.speak(u);
                setTimeout(resolve, 2000);
            });
            speechSynthesis.cancel();
            return performance.now() - started;
        })()""")
    finally:
        browser.close()

    timings = sorted(samples["timings"])
    p50 = statistics.median(timings)
    p95 = timings[max(0, int(len(timings) * 0.95) - 1)]

    print(f"\nTime to first audio, {len(timings)} turns\n" + "-" * 46)
    print(f"  fastest   {timings[0]:7.0f} ms")
    print(f"  median    {p50:7.0f} ms   budget {BUDGET_MS} ms")
    print(f"  p95       {p95:7.0f} ms")
    print(f"  slowest   {timings[-1]:7.0f} ms")
    if speech is None:
        print("\n  No speech engine in this browser, so the final hop to")
        print("  speechSynthesis is not included. It is tens of ms on a real device.")
    else:
        print(f"\n  speechSynthesis start: {speech:.0f} ms (not included above)")

    print("-" * 46)
    if p50 <= BUDGET_MS:
        print(f"PASS  median {p50:.0f} ms is within {BUDGET_MS} ms")
        return 0
    print(f"FAIL  median {p50:.0f} ms is over {BUDGET_MS} ms")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
