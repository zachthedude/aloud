"""Unit tests for client logic, run in a real browser.

There is no Node on this machine, so there is no jest and no vitest. What
there is, is Chrome, which is where this code actually runs anyway. Each case
below is a snippet evaluated against the real ES modules the app serves, and
it either returns true or it returns why it did not.

    python tools/jscheck.py --base http://127.0.0.1:8000

This exists for logic that cannot be reached from the journey tool: maths that
only fires with a microphone in front of it, or a timing rule that would take
a real minute to observe. Everything else belongs in journey.py, driving the
real interface.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from qa import Browser, find_chrome  # noqa: E402

# Each case: (name, expression). The expression returns true, or a string
# explaining the failure, and is evaluated with the module already imported.
CASES: list[tuple[str, str, str]] = []


def case(module: str, name: str):
    def register(body: str):
        CASES.append((module, name, body))
    return register


# ---------------------------------------------------------------------------
# Barge-in: the student talking over the teacher
# ---------------------------------------------------------------------------

BARGE = "/js/core/bargein.js"

case(BARGE, "a sustained voice interrupts")("""
  const d = new M.InterruptDetector();
  d.start(0);
  // Calibrate on a quiet room for 700ms.
  for (let t = 0; t < 700; t += 16) d.push(0.02, t);
  let fired = -1;
  for (let t = 700; t < 1400; t += 16) {
    if (d.push(0.4, t)) { fired = t; break; }
  }
  if (fired < 0) return 'never fired';
  // It should take about the sustain window, not longer.
  return fired <= 700 + 220 || `took ${fired - 700}ms`;
""")

case(BARGE, "a short bang does not interrupt")("""
  const d = new M.InterruptDetector();
  d.start(0);
  for (let t = 0; t < 700; t += 16) d.push(0.02, t);
  // 100ms of loud, then quiet again. A door, a cough, a dropped book.
  for (let t = 700; t < 800; t += 16) if (d.push(0.6, t)) return 'fired on a bang';
  for (let t = 800; t < 1600; t += 16) if (d.push(0.02, t)) return 'fired after a bang';
  return true;
""")

case(BARGE, "Aloud's own voice never interrupts itself")("""
  // The realistic failure: the teacher is playing out of the speaker and
  // leaking into the microphone at a steady level the whole time. Calibration
  // happens while that is already playing, so it becomes the floor.
  const d = new M.InterruptDetector();
  d.start(0);
  const leak = () => 0.22 + Math.sin(Date.now()) * 0.01;
  for (let t = 0; t < 700; t += 16) d.push(leak(), t);
  for (let t = 700; t < 6000; t += 16) {
    if (d.push(leak(), t)) return `interrupted itself at ${t}ms`;
  }
  return true;
""")

case(BARGE, "a voice still cuts through Aloud's own leakage")("""
  const d = new M.InterruptDetector();
  d.start(0);
  for (let t = 0; t < 700; t += 16) d.push(0.22, t);   // teacher playing
  for (let t = 700; t < 1400; t += 16) {
    if (d.push(0.45, t)) return true;                   // student talks over it
  }
  return 'a real voice did not get through';
""")

case(BARGE, "nothing fires during the grace period")("""
  const d = new M.InterruptDetector({ calibrateMs: 100, graceMs: 500 });
  d.start(0);
  for (let t = 0; t < 100; t += 16) d.push(0.02, t);
  for (let t = 100; t < 500; t += 16) if (d.push(0.9, t)) return `fired at ${t}ms`;
  return true;
""")

case(BARGE, "it fires once, not every frame")("""
  const d = new M.InterruptDetector();
  d.start(0);
  for (let t = 0; t < 700; t += 16) d.push(0.02, t);
  let count = 0;
  for (let t = 700; t < 3000; t += 16) if (d.push(0.5, t)) count += 1;
  return count === 1 || `fired ${count} times`;
""")

case(BARGE, "silence can never interrupt, however quiet the room")("""
  const d = new M.InterruptDetector();
  d.start(0);
  // A perfectly silent room: ambient 0, so the floor is the absolute one.
  for (let t = 0; t < 700; t += 16) d.push(0, t);
  for (let t = 700; t < 3000; t += 16) if (d.push(M.FLOOR - 0.01, t)) return 'fired on silence';
  return true;
""")

case(BARGE, "a reset disarms it")("""
  const d = new M.InterruptDetector();
  d.start(0);
  for (let t = 0; t < 700; t += 16) d.push(0.02, t);
  d.reset();
  for (let t = 700; t < 3000; t += 16) if (d.push(0.9, t)) return 'fired after reset';
  return true;
""")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run client-side unit checks in Chrome.")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    browser = Browser(find_chrome())
    failures = 0
    try:
        browser.send("Page.enable")
        browser.send("Runtime.enable")
        browser.send("Page.navigate", {"url": f"{base}/"})
        browser.drain(1.5)

        print(f"\nClient checks against {base}\n" + "-" * 66)
        for module, name, body in CASES:
            expression = (
                f"(async () => {{ const M = await import('{module}');"
                f" return (function () {{ {body} }})(); }})()"
            )
            result = browser.send(
                "Runtime.evaluate",
                {"expression": expression, "awaitPromise": True, "returnByValue": True},
                timeout=30,
            )
            details = result.get("exceptionDetails")
            if details:
                value = details.get("exception", {}).get("description") or details.get("text")
            else:
                value = result.get("result", {}).get("value")

            if value is True:
                print(f"  OK   {name}")
            else:
                failures += 1
                print(f"  FAIL {name}: {value}")
    finally:
        browser.close()

    print("-" * 66)
    print(f"{len(CASES) - failures}/{len(CASES)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
