# Progress

Updated after every feature. Three columns that matter: what is done, what is
tested, what is known to be rough. See `PLAN.md` for the plan this tracks.

**Baseline commit:** `f1c6fb3` — the self-paced app before the crunch rebuild.
Anything the rebuild deletes is one `git revert` away.

**Now:** 427 tests · 14 client checks · 38/38 screens clean in light and dark at
1440 and 390 · browser journey 19/19 · time to first audio median 431 ms.

---

## Phase 0 — repo hygiene · done

| Item | State |
|---|---|
| git repository + baseline commit | done |
| Feature flags (`app/features.py`, `ALOUD_FEATURES`) | done |
| Flags exposed to the client on `/api/auth/me` | done |
| `PLAN.md`, `PROGRESS.md` | done |
| Playwright for Python | installed, 1.63.0 with Chromium |
| Context7 MCP | installed and connected |
| Tesseract OCR | **blocked** — needs admin rights, see below |

**Rough:** Tesseract could not be installed unattended. winget downloaded and
verified it, then the install failed with `0x800704c7` because the UAC prompt
was not accepted. Run from a terminal that can elevate:

    winget install --id UB-Mannheim.TesseractOCR -e
    python -m pip install pytesseract

---

## Phase A — ingestion

### A1 · Resource roles · done

Every upload is tagged `notes`, `past paper`, `mark scheme` or `syllabus`.
Guessed from the filename, refined once from the document's opening, never
overwriting a role set by hand. Correctable from the material list.

The guess is deliberately timid: `notes` is the default and the other three
need positive evidence, because the examiner must never mark against a "mark
scheme" that is really a set of revision notes.

**Tested:** 15 unit, 4 API.

### A2 · OCR · done, with one caveat

Photos (`png jpg jpeg webp gif bmp`) import and are signature-checked like
every other format. Two readers tried in order: Tesseract locally, then Claude
vision. When neither is present the image is refused with a message naming
both fixes.

**Tested:** 12 unit, including the honest-failure path and a JPEG renamed to
`.png` being refused.

**Rough:** neither reader is installed on this machine right now, so in
practice every photo currently hits the refusal path. That path is the tested
one, and it is the correct behaviour, but nobody has yet watched a real
whiteboard photo turn into a lesson.

### A3 · Mark weightings · done

Each concept carries `exam_weight` and the sentence that justifies it. Past
papers give frequency, the syllabus gives examinability, and the basis reads
like something a teacher would say: "came up in 3 of 4 past papers; named in
the syllabus".

With neither uploaded, every topic weighs the same and the basis says exactly
that. A topic never asked keeps a floor, because absent from four papers is
weak evidence, not proof.

**Tested:** 14 unit.

**Rough:** matching is lexical. A paper that says "F = ma" without naming
Newton's second law will not register. Good enough to rank, not good enough to
quote a mark count from, which is why nothing claims a mark count.

### A4 · Embedding cache · not started

---

## Ways of working · done

All sixteen modes are now visible in the app, in the nav under **Ways of
working**. Each shows its icon, what it does in one sentence, and its state:

- **On / Off** — built and available. The toggle is a real checkbox and the
  choice is stored per student.
- **Needs setup** — built, but missing something. It says what, for example
  "needs an ANTHROPIC_API_KEY to judge an answer properly".
- **Being built** — not implemented yet. It cannot be switched on.

`app/services/modes.py` is the catalogue only. The strategies land in phase D;
keeping the description separate is what lets the screen show the whole shape
of the product without pretending any of it works.

**Tested:** 10 unit on the catalogue, plus the modes screen in the QA sweep.

**Rough:** all sixteen flags default off, so out of the box the screen is
honest but dull. To see it populated:
`ALOUD_FEATURES=triage,heatmap,panic,examiner,liar,coldcall,teachback,last90 python run.py`

---

## Hum, the guide · done

A small companion in the bottom corner that says one useful thing about
wherever you are, and will read it out. It exists because a voice-first
product cannot rely on tooltips: a student with the screen off cannot see one.

Three rules keep it from being Clippy: it says one thing once, it only speaks
when asked, and when it is dismissed it stays dismissed.

**Rough:** the guidance lines are a static map from route to sentence. It
knows where you are, not what you are doing, so it cannot say "you have not
uploaded a mark scheme yet". That needs the learner model (phase C).

---

## Phase B — the voice loop

### B1 · Barge-in and push-to-talk · done

The student can talk over the teacher and it stops instantly.

The hard part was never detecting speech. It was not detecting *Aloud's own
speech*, playing out of the same device the microphone points at. Four
defences: `echoCancellation` on the stream, calibration that happens while
Aloud is already talking so leakage becomes the floor, a grace period before
arming, and a 180 ms sustain window so a door closing cannot trigger it.

Barge-in only arms after the microphone has been used successfully once in
the session. Asking for microphone permission the moment a lesson starts, for
a feature nobody requested, is how you get denied forever.

**Hold H** is the push-to-talk fallback, and the only way in when barge-in has
never armed.

**Tested:** 8 client checks in `tools/jscheck.py`, including the one that
matters: five seconds of Aloud's own leakage never interrupting itself, and a
real voice still cutting through it.

**Rough:** every check is synthetic. The detector has never met a real
microphone, a real room or a phone speaker. Thresholds are reasoned, not
tuned. This needs a human with a phone on a table, and it is the single most
likely thing in this build to be wrong.

### B2 · Surviving the screen going off · done

Wake Lock while narrating, released on pause. Media Session so the lock screen
and headphone buttons work. A silent looping track so a backgrounded tab is
not throttled into silence. All three are best-effort and none throws.

Plus a PWA: manifest, installable, and a service worker that caches the shell
only. Lessons and progress are never cached, because a stale answer about what
you know is worse than an honest "you are offline".

**Rough:** untested on a real phone. That is the whole point of the feature, so
it counts for very little until someone walks around with it.

### B3 · Streaming · deliberately not built

`tools/latency.py` drives 20 real turns through the real endpoint:

    fastest 260 ms · median 424 ms · p95 830 ms · budget 1200 ms

That is the on-device engine, which is what runs with no API key. It passes
with room to spare, so the streaming turn endpoint in `PLAN.md` would be
plumbing built for a problem that does not exist yet on this path.

**The gap:** with `ANTHROPIC_API_KEY` set, the turn becomes a Claude round
trip and this number will not hold. Streaming is what fixes that, and it
should be built and measured against a real key rather than guessed at. Run
`python tools/latency.py` with a key set to see where it actually lands.

## Phase C — the learner model · done

One row per student per concept, and one service allowed to write it. Every
answer, spoken or typed, now lands correctness, time to start answering,
filler words and self-corrections in a single upsert, so an answer can never
be half-recorded.

**What is stored** is only what was observed: answers by outcome (correct,
partial, wrong, missed), the last eight response times, fillers,
self-corrections, lies caught, and quarantine state.

**What is derived**, on read, because it depends on the clock:

- **confidence** — smoothed accuracy, discounted by hesitation, faded by time
  since last seen. One right answer is promising, not proof.
- **hesitation** — how long they took to start, plus fillers and corrections.
  Independent of whether the answer was right.
- **shaky** — right, but hesitant, over at least two answers. The signal
  feature 4 exists to surface.
- **quarantine** — three misses in, two clean answers in a row out. Feature
  15's rules, live now.

**Forgetting.** Settings has "Forget what Aloud knows about me": the learner
model, every transcript, every reply and every assessment, deleted in one
transaction. Uploads and lessons stay. It asks once before doing it, because
it cannot be undone, so it is one tap plus one confirmation rather than
strictly one tap.

**Decision changed from the plan:** the signals are columns on the existing
`mastery` row rather than a separate table. Two tables about the same student
and concept would eventually disagree, which is the one failure this phase
exists to prevent. A test enforces that only `learner_service.py` writes it.

**Bug found and fixed on the way:** a student answering quickly could have
their answer treated as a chat question and never marked. The server only
counted text as an answer when its own copy of the session said "waiting for
the student", and the client saves that asynchronously, so a fast answer
could overtake it. The client now says a question is open when it knows one
is. Found because the browser journey failed one run in several; covered by a
regression test.

**Tested:** 36 unit tests on the maths, 11 API tests over real HTTP (every
signal in one row, typed answers carry no latency, the progress screen and the
learner model agree, forgetting removes everything and only for you, strangers
get 404), 6 client checks on how latency is measured, the migration run
against the live v4 database with all rows kept, and a mechanical test that
nothing else writes the learner row.

**Rough:**
- Filler counting reads the transcript, and Chrome's recogniser often strips
  "um" and "uh" before we see them. It undercounts; it never invents.
- "Unclear" counts as a miss. That covers an honest "I don't know", but it
  also covers the evaluator being unsure, which can put a strike on a concept
  the student may actually know.
- Nothing visible reads the model yet except Settings. The hesitation heatmap
  (feature 4) is what puts it on screen.

## Phase D — the orchestrator and the room · done

Every mode is now a small strategy over one voice loop and one learner model.
The plan's test for done holds: **adding a mode is one file and one line in
`app/services/modes/registry.py`**, and no mode owns a microphone, a speech
queue, a repository or an engine.

**How it fits together**

- `app/domain/turn.py`: what passes between a mode and the loop. A mode
  receives a `Reply` and returns a `Turn`: what to say, whether to listen,
  which concept, which material it came from, and whether it is finished.
- `app/services/modes/base.py`: the `Mode` contract and `ModeContext`, which
  hands a mode the learner model and the functions to evaluate and record an
  answer. That is all a mode can touch.
- `app/services/crunch_service.py`: the orchestrator. Checks the mode may
  run, evaluates, writes every answer through `LearnerService`, keeps the
  transcript, and persists the mode's place so a closed tab resumes.
- `web/js/core/voiceloop.js`: the client loop. Speak, listen, send, speak.
  Borrows the same narrator, recogniser, barge-in and keep-alive the lesson
  player uses, and hands them back on the way out.
- `web/js/views/room.js`: the one screen every mode runs in.

**Two real modes prove it**, deliberately opposite shapes:

- **Quarantine (15)** asks, marks and records. Worst topic first, interleaved
  rather than hammered, and a sitting ends even if nothing clears.
- **Last 90 seconds (2)** only speaks. Concepts the learner model says are
  known, heaviest in the exam first, fitted to 90 seconds at the student's
  own speed. It never asks and never writes to the learner model.

**The room.** The spoken line is the interface, set large in the serif,
with earlier lines drifting up and dimming. What the student said is set in
the sans, so who is speaking is carried by the type rather than a label.
Brass means Aloud has the floor; the cool colour means it is yours; the same
state is always said in words too. One big talk button, then type, repeat,
skip and leave. Contrast measured on the night ground: secondary text 8.6:1,
errors 9.2:1, the your-turn colour 7.6:1.

**Rules enforced by tests, not by hope:** no turn is longer than three
sentences, every turn that names a concept says which material it came from,
feedback never scolds, a flag cannot switch on a mode that is not built, and
a built mode defaults on while an unbuilt one defaults off.

**Found and fixed on the way:**
- The service worker served scripts **cache-first**, and nothing here is
  content-hashed, so after it installed, changes were invisible until the
  cache was renamed by hand. Now network-first, with the cache as the offline
  fallback.
- The modes screen showed a mode as On whenever its flag was set, even if it
  did not exist. It now uses the same rule as the orchestrator, which is why
  it now says 2 ready rather than 7.
- The room's error text borrowed the page's red, which in light mode is dark
  and unreadable on the room's night background.

**Tested:** 34 unit tests on the two modes and the contract, 14 API tests
over real HTTP (a real quarantined concept asked, answered and recorded once;
resume after a closed tab; unbuilt, invented and switched-off modes refused
with the reason; strangers get 404; forgetting removes sittings), a journey
step that runs quarantine through the real room and checks the learner model
recorded exactly one answer, and both room screens in the QA sweep.

**Deliberately not done, and why:**
- **The old dashboard, plan, library and progress screens are still there.**
  The approved plan folds them into the room, but with two modes the room
  cannot yet do their jobs, and removing them now would make the app worse.
  They go once triage and the heatmap replace them.
- **The lesson player still drives the audio modules itself** rather than
  through the voice loop. They take turns safely, because the room pauses
  any lesson on the way in. It moves onto the loop when lessons become a
  mode. Marked with a `ponytail:` comment in `voiceloop.js`.
- **Streaming** is still not built, for the reason in B3.

**Rough:**
- Headless Chrome has no voice, so the room's spoken path is only exercised
  by hand. The journey hands the floor over the way the narrator would.
- The demo data was given a learner history through the real maths, so the
  modes have something to do. Applied to the demo account only: the database
  also holds a real account, so there was no reset.
- The two oldest history lines in the room fade to 4.1:1 contrast on
  purpose. They are hidden from screen readers and always in the transcript,
  so no required reading depends on them.

## Features 1–16

| # | Mode | State |
|---|---|---|
| 2 | Last 90 seconds | **done**, in phase D |
| 15 | Quarantine | **done**, in phase D |
| 1, 3–14, 16 | everything else | visible, honest, not built |

Next in the brief's order: **1 Triage**, **4 Hesitation heatmap**, **7 Examiner**.
