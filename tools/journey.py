"""The student journey, driven in a real browser.

Walks the path the product is judged on, through the actual client code rather
than the API: open a lesson, play it, reach a question, answer it, get a reply,
adapt, finish, and resume from the right place afterwards.

    python tools/journey.py --base http://127.0.0.1:8111

Headless Chrome throttles requestAnimationFrame hard, so narration is not
waited on in real time. Each step drives the controller and asserts on the
store, which is what the interface itself renders from.

ponytail: runs against the live demo database and leaves the lesson completed,
so a second run in a row can fail at the finish step. Re-seed between runs
(`python run.py --seed-only` after `--reset`) if that matters; a dedicated
throwaway account per run is the fix if it ever stops being a dev-only tool.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qa import Browser, find_chrome, sign_in  # noqa: E402


def evaluate(browser: Browser, expression: str, *, timeout: float = 45.0):
    result = browser.send(
        "Runtime.evaluate",
        {"expression": expression, "awaitPromise": True, "returnByValue": True},
        timeout=timeout,
    )
    details = result.get("exceptionDetails")
    if details:
        raise AssertionError(details.get("exception", {}).get("description") or details.get("text"))
    return result.get("result", {}).get("value")


STEPS: list[tuple[str, str]] = []
BASE_URL = ["http://127.0.0.1:8111"]


def step(name: str):
    def register(func):
        STEPS.append((name, func))
        return func
    return register


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk the student journey in a browser.")
    parser.add_argument("--base", default="http://127.0.0.1:8111")
    parser.add_argument("--email", default="student@aloud.app")
    parser.add_argument("--password", default="learnaloud")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    BASE_URL[0] = base

    token = sign_in(base, args.email, args.password)
    opener = urllib.request.build_opener()
    opener.addheaders = [("Cookie", f"aloud_session={token}")]

    def api(path: str):
        with opener.open(f"{base}{path}", timeout=20) as response:
            return json.loads(response.read())

    spaces = api("/api/spaces")["spaces"]
    if not spaces:
        print("No subjects. Run: python run.py --seed-only")
        return 2
    space_id = spaces[0]["id"]
    detail = api(f"/api/spaces/{space_id}")
    # A lesson that actually contains a question to answer.
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
    failures = 0
    try:
        browser.send("Page.enable")
        browser.send("Runtime.enable")
        browser.send("Network.enable")
        browser.send("Network.setCookie", {
            "name": "aloud_session", "value": token, "domain": "127.0.0.1", "path": "/",
        })
        browser.send("Emulation.setDeviceMetricsOverride", {
            "width": 1280, "height": 860, "deviceScaleFactor": 1, "mobile": False,
        })
        browser.send("Page.navigate", {"url": f"{base}/app"})
        browser.drain(2.5)

        # Make the controller and store reachable from the console.
        evaluate(browser, """(async () => {
            const lp = await import('/js/core/lessonplayer.js');
            const st = await import('/js/core/store.js');
            const nr = await import('/js/core/narrator.js');
            window.__t = { lp: lp.lessonPlayer, store: st.store, player: st.player,
                          setPlayer: st.setPlayer, narrator: nr.narrator };
            // Headless has no speech engine worth waiting on; the narrator
            // already falls back to advancing the clock silently.
            return true;
        })()""")

        print(f"\nJourney through lesson {lesson_id}\n" + "-" * 66)
        for name, func in STEPS:
            try:
                detail_text = func(browser, lesson_id, space_id)
                print(f"  OK   {name:<42} {detail_text}")
            except AssertionError as error:
                failures += 1
                print(f"  FAIL {name:<42} {error}")
            except Exception as error:  # noqa: BLE001
                failures += 1
                print(f"  FAIL {name:<42} {type(error).__name__}: {error}")
    finally:
        browser.close()

    print("-" * 66)
    print(f"{len(STEPS) - failures}/{len(STEPS)} steps passed")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# The journey
# ---------------------------------------------------------------------------


@step("Appearance switches and persists")
def theme(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        const read = () => ({
            attr: document.documentElement.dataset.theme,
            mode: window.__aloudTheme.mode,
            bg: getComputedStyle(document.body).backgroundColor,
        });
        const seen = [];
        for (const mode of ['light', 'dark', 'system']) {
            window.__aloudTheme.set(mode);
            // The ground colour transitions, so settle before measuring it.
            await new Promise(r => setTimeout(r, 650));
            seen.push(read());
        }
        return { seen, stored: localStorage.getItem('aloud-theme'),
                 hasToggle: Boolean(document.querySelector('.theme-toggle, .topbar .btn-icon')) };
    })()""")
    light, dark = state["seen"][0], state["seen"][1]
    assert light["attr"] == "light" and dark["attr"] == "dark", state["seen"]
    assert light["bg"] != dark["bg"], "the ground colour did not actually change"
    assert state["stored"] == "system", f"choice not persisted: {state['stored']}"
    assert state["hasToggle"], "no appearance control on screen"
    return f"{light['bg']} <-> {dark['bg']}, persisted"


@step("Open a lesson")
def open_lesson(browser, lesson_id, _space_id):
    state = evaluate(browser, f"""(async () => {{
        await window.__t.lp.open('{lesson_id}');
        await new Promise(r => setTimeout(r, 700));
        const p = window.__t.player();
        return {{ title: p.lesson && p.lesson.title, sections: p.sections.length,
                 open: p.open, state: p.state }};
    }})()""")
    assert state["open"], "player did not open"
    assert state["sections"] > 0, "lesson has no sections"
    return f"{state['title']!r}, {state['sections']} parts"


@step("Press play")
def press_play(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        window.__t.lp.play();
        await new Promise(r => setTimeout(r, 600));
        const p = window.__t.player();
        return { state: p.state, chunks: window.__t.narrator._chunks.length };
    })()""")
    # Headless Chrome has had no user gesture, so the speech engine may refuse
    # and the player falls back to paused with a message. That is the correct
    # behaviour; what matters is that narration was actually started.
    assert state["chunks"] > 0, "the narrator was never given anything to say"
    assert state["state"] in ("playing", "ai_speaking", "paused"), f"state is {state['state']}"
    note = "" if state["state"] != "paused" else " (speech blocked, no user gesture)"
    return f"state={state['state']}{note}"


@step("Change speed while playing")
def change_speed(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        window.__t.lp.setSpeed(1.5);
        await new Promise(r => setTimeout(r, 400));
        return { speed: window.__t.player().speed, rate: window.__t.narrator.rate };
    })()""")
    assert abs(state["speed"] - 1.5) < 0.01, f"speed is {state['speed']}"
    assert abs(state["rate"] - 1.5) < 0.01, "narrator rate did not follow"
    return "1.5x, narrator in step"


@step("Cycle through the voices")
def cycle_voices(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        const voices = window.__t.narrator.voices();
        if (voices.length < 2) return { skipped: voices.length };
        const seen = [];
        for (let i = 0; i < 3; i += 1) {
            const next = window.__t.lp.cycleVoice({ preview: false });
            seen.push(next && next.name);
        }
        return { count: voices.length, seen, stored: window.__t.player().voice };
    })()""", timeout=45)
    if "skipped" in state:
        return f"only {state['skipped']} voice installed, nothing to cycle"
    assert len(set(state["seen"])) > 1, f"the voice never changed: {state['seen']}"
    assert state["stored"] == state["seen"][-1], "the store did not follow the narrator"
    return f"{state['count']} voices, cycled to {state['seen'][-1][:28]!r}"


@step("Pause and resume")
def pause_resume(browser, _lesson_id, _space_id):
    paused = evaluate(browser, """(async () => {
        window.__t.lp.pause();
        await new Promise(r => setTimeout(r, 300));
        return window.__t.player().state;
    })()""")
    assert paused == "paused", f"state is {paused}"
    resumed = evaluate(browser, """(async () => {
        window.__t.lp.play();
        await new Promise(r => setTimeout(r, 400));
        return window.__t.player().state;
    })()""")
    assert resumed in ("playing", "ai_speaking"), f"state is {resumed}"
    return "paused then resumed"


@step("Reach a question")
def reach_question(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        const p = window.__t.player();
        const index = p.sections.findIndex(s => s.question);
        window.__t.lp.goToSection(index, { autoplay: false });
        await new Promise(r => setTimeout(r, 300));
        // Finishing the narration of a check beat hands over the floor.
        window.__t.narrator.onEnd();
        await new Promise(r => setTimeout(r, 500));
        const after = window.__t.player();
        return { state: after.state, prompt: after.question && after.question.prompt };
    })()""")
    assert state["state"] == "waiting_for_student", f"state is {state['state']}"
    assert state["prompt"], "no question on the floor"
    return f"{state['prompt'][:44]!r}"


@step("The room turns to the student")
def room_turns(browser, _lesson_id, _space_id):
    holder = evaluate(browser, """(() => {
        const el = document.querySelector('.player');
        return el && el.dataset.holder;
    })()""")
    assert holder == "student", f"holder is {holder}"
    return "colour says it is your turn"


@step("Answer the question")
def answer_question(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        const p = window.__t.player();
        await window.__t.lp.submitAnswer(
            'a force is a push or a pull that acts on an object and is measured in newtons',
            'text'
        );
        await new Promise(r => setTimeout(r, 1800));
        const after = window.__t.player();
        return { state: after.state, reply: after.reply && after.reply.text,
                 judgement: after.reply && after.reply.judgement,
                 mastery: after.reply && after.reply.mastery };
    })()""", timeout=60)
    assert state["reply"], "the teacher said nothing back"
    assert state["judgement"], "no judgement was recorded"
    return f"{state['judgement']} -> {state['mastery']!r}"


@step("The reply is never scolding")
def reply_is_kind(browser, _lesson_id, _space_id):
    text = evaluate(browser, "window.__t.player().reply.text.toLowerCase()")
    for word in ("wrong", "failed", "bad answer", "should have", "behind"):
        assert word not in text, f"reply contains {word!r}"
    return "no scolding language"


@step("Ask the teacher mid-lesson")
def ask_teacher(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        await window.__t.lp.ask('explain that more simply');
        await new Promise(r => setTimeout(r, 2000));
        const p = window.__t.player();
        return { reply: p.reply && p.reply.text, state: p.state };
    })()""", timeout=60)
    assert state["reply"], "no explanation came back"
    return f"{state['reply'][:44]!r}"


@step("A voice command moves the player, not the chat")
def voice_command(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        const before = window.__t.player().speed;
        await window.__t.lp.ask('slow down');
        await new Promise(r => setTimeout(r, 1200));
        return { before, after: window.__t.player().speed };
    })()""", timeout=45)
    assert state["after"] < state["before"], f"speed went {state['before']} -> {state['after']}"
    return f"{state['before']}x -> {state['after']}x"


@step("Position is saved to the server")
def position_saved(browser, lesson_id, _space_id):
    state = evaluate(browser, f"""(async () => {{
        window.__t.lp.goToSection(2, {{ autoplay: false }});
        window.__t.setPlayer({{ elapsed: 12.5 }});
        await window.__t.lp._save({{ force: true }});
        await new Promise(r => setTimeout(r, 600));
        const response = await fetch('/api/lessons/{lesson_id}', {{ credentials: 'same-origin' }});
        const data = await response.json();
        return {{ ordinal: data.session.sectionOrdinal, position: data.session.positionSeconds }};
    }})()""", timeout=45)
    assert state["ordinal"] == 2, f"server has ordinal {state['ordinal']}"
    return f"part {state['ordinal']}, {state['position']}s"


@step("Leaving keeps the mini player")
def mini_player(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        window.__t.lp.close();
        await new Promise(r => setTimeout(r, 500));
        const mini = document.querySelector('.miniplayer');
        return { hasMini: Boolean(mini), lessonId: window.__t.player().lessonId,
                 title: mini && mini.querySelector('.miniplayer-title').textContent };
    })()""")
    assert state["hasMini"], "the mini player did not appear"
    assert state["lessonId"], "the lesson was dropped"
    return f"{state['title']!r} still playing"


@step("Reopening resumes in the same place")
def resume(browser, lesson_id, _space_id):
    state = evaluate(browser, f"""(async () => {{
        window.__t.lp.exit();
        await new Promise(r => setTimeout(r, 400));
        await window.__t.lp.open('{lesson_id}');
        await new Promise(r => setTimeout(r, 900));
        const p = window.__t.player();
        return {{ ordinal: p.sectionIndex, speed: p.speed }};
    }})()""", timeout=45)
    assert state["ordinal"] == 2, f"resumed at part {state['ordinal']}"
    return f"resumed at part {state['ordinal']}, {state['speed']}x"


@step("Finish the lesson")
def finish(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        await window.__t.lp.finish();
        await new Promise(r => setTimeout(r, 1800));
        const p = window.__t.player();
        const a = p.finished && p.finished.assessment;
        return { state: p.state, outcome: a && a.outcomeLabel,
                 recommendation: a && a.recommendation,
                 takeaways: a ? a.takeaways.length : 0 };
    })()""", timeout=60)
    assert state["state"] == "completed", f"state is {state['state']}"
    assert state["outcome"], "no understanding state was assigned"
    assert state["recommendation"], "no recommendation was made"
    return f"{state['outcome']!r}, {state['takeaways']} takeaways"


@step("Progress reflects the session")
def progress(browser, _lesson_id, _space_id):
    state = evaluate(browser, """(async () => {
        const response = await fetch('/api/progress', { credentials: 'same-origin' });
        const data = await response.json();
        const touched = data.spaces.flatMap(s => s.concepts).filter(c => c.evidence > 0);
        return { touched: touched.length, first: touched[0] && touched[0].masteryLabel };
    })()""")
    assert state["touched"] > 0, "no concept shows any evidence"
    return f"{state['touched']} concepts with evidence"


@step("Scrolling travels along the background wave")
def waveform_backdrop(browser, _lesson_id, _space_id):
    """Last, because it leaves the app for the landing page."""
    browser.send("Page.navigate", {"url": BASE_URL[0] + "/"})
    browser.drain(2.5)
    state = evaluate(browser, """(async () => {
        const wave = document.querySelector('.wave-backdrop');
        if (!wave) return { missing: true };
        const settle = () => new Promise(r => setTimeout(r, 150));
        const sections = [...document.querySelectorAll('.screen')];
        const landed = [];
        for (const section of sections) {
            window.scrollTo({ top: section.offsetTop, behavior: 'instant' });
            await settle();
            landed.push(wave.__update().phrase);
        }
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' });
        await settle();
        const bottom = wave.__update();
        return { count: sections.length, landed, atEnd: Math.abs(bottom.x - bottom.end) < 1 };
    })()""", timeout=60)
    assert not state.get("missing"), "no wave behind the landing page"
    expected = list(range(state["count"]))
    assert state["landed"] == expected, f"sections map to phrases {state['landed']}, expected {expected}"
    assert state["atEnd"], "the bottom of the page is not the end of the wave"
    return f"{state['count']} sections, one phrase each, ends with the page"


if __name__ == "__main__":
    raise SystemExit(main())
