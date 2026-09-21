# Aloud — exam-crunch rebuild

**Goal:** turn Aloud from a calm self-paced learning path into a voice-first
exam-crunch tutor: upload the material, put the phone down, get taught,
quizzed and coached out loud in the hours before the exam.

**Spec:** the brief in the conversation of 2026-09-21. This plan argues from it.

**For agentic workers:** every task ends with tests run, a demo script, a code
review pass and a security pass. Steps are checkboxes so progress is trackable.
`PROGRESS.md` is updated after every feature, not at the end.

---

## Global constraints

Copied verbatim from the brief. Every task inherits these.

- **Grounding.** Every teaching claim traces to an uploaded section id. If the
  material does not cover it, Aloud says so aloud. Never fabricate facts, marks
  or past-paper patterns.
- **Privacy.** Audio is never stored by default. Transcripts and the learner
  model are deletable in one tap. No API keys in the repo, ever.
- **Cost.** Cache embeddings, cap tokens per turn, stream everything, log cost
  per session.
- **Accessibility.** Full keyboard and screen-reader support, captions for
  everything Aloud says, adjustable speech speed.
- **Tone.** Warm, brief, direct, a little dry. Never patronising. Never more
  than three sentences at a time unless the learner asks for more.
- **Latency.** Under 1.2s from end-of-speech to first audio, measured, not
  asserted.

---

## Decisions

### Locked by the environment

**No Node, npm or npx on this machine** (verified). That removes Expo, React,
Vite and Playwright-JS. It is not a preference; it is the machine.

So: **a PWA on the existing Python + zero-build ES-module stack.** This reaches
everything the brief needs — `wakeLock`, `mediaSession`, `DeviceMotionEvent`,
`getUserMedia`, `speechSynthesis`, service-worker offline — through browser
APIs that need no build step. End-to-end tests use **Playwright for Python**,
which installs its own Chromium and needs no Node.

*Five-line justification, as asked:* FastAPI gives async streaming (SSE) and
runs the ingestion that already works; SQLite needs no server and the whole
product is single-user-per-account; zero-build ES modules mean no toolchain to
break and a hard CSP with no CDN; the browser's own speech engine gives
sub-300ms first audio for free, offline, at zero marginal cost; and every one
of the 16 features is reachable from a mobile browser without an app store.

### Locked by your answers

1. **Refocus onto crunch.** The exam date becomes the centre of the product.
   `dashboard`, `plan`, `library` and `progress` views collapse into one crunch
   surface plus a material drawer. The old views are deleted, not hidden — dead
   UI rots. The lesson player survives as the *narration* layer underneath the
   voice loop.
2. **Claude key with graceful degrade.** `ANTHROPIC_API_KEY` in `.env`, never
   committed. Each mode declares `requires_model` and `requires_vision`. Without
   a key the app *says which modes are unavailable* and disables them. It never
   silently substitutes a worse answer for a marked one.
3. **Browser speech, streaming shell.** `speechSynthesis` behind a
   `VoiceOut` interface so a hosted TTS can be dropped in later without touching
   any mode.

### Decisions I am making without asking

- **The learner model is one table, written through one service.** Any mode
  reaching into `concept_signals` directly is a bug. This is the single most
  load-bearing decision in the plan: 14 of the 16 features read it.
- **Modes are strategies, not pipelines.** One audio path, one turn endpoint,
  one session record. A mode only decides *what to say next and what to expect
  back*.
- **Feature flags are server-side and per-user**, exposed at `/api/me`, so a
  half-built mode cannot be reached by typing a URL.
- **Every mode is pure where it can be.** Mode logic takes state and returns a
  turn; the service does the I/O. That is what makes 16 features testable
  without a microphone.

---

## Architecture

### The spine

```
           upload  ─────────────►  ingestion  ─────────►  topic graph
                                   (A)                     + weightings
                                                                │
   mic ──► VoiceLoop ──► /api/crunch/{id}/turn ──► Orchestrator (D)
    ▲        (B)              SSE stream               │
    │                                                  ├─► Mode strategy
    └──── speech out ◄───────────────────────────────  │   (1-16)
                                                       │
                                            Learner model (C)
                                            read by every mode,
                                            written by every turn
```

### File map

**New backend**

| File | Responsibility |
|---|---|
| `app/domain/learner.py` | Pure signal maths: confidence, hesitation, quarantine. No I/O. |
| `app/domain/triage.py` | Hours-left maths, topic ranking, time-boxed plan, skip reasons. |
| `app/domain/turn.py` | The `Turn` value object every mode returns. |
| `app/services/learner_service.py` | The only writer of `concept_signals`. |
| `app/services/crunch_service.py` | Session lifecycle, mode dispatch, SSE turn streaming. |
| `app/services/modes/base.py` | `Mode` protocol + `ModeContext`. |
| `app/services/modes/<name>.py` | One file per mode, 1–16. |
| `app/services/modes/registry.py` | key → Mode, plus `available()` given providers. |
| `app/services/weighting.py` | Mark weightings from syllabus headings + past-paper frequency. |
| `app/services/documents/ocr.py` | Image → text: pytesseract, else Claude vision, else honest failure. |
| `app/services/cost.py` | Token and cost accounting per session. |
| `app/api/routes_crunch.py` | Session start, turn (SSE), photo, end. |
| `app/api/routes_learner.py` | Heatmap read, one-tap delete. |

**New frontend**

| File | Responsibility |
|---|---|
| `web/js/core/voiceloop.js` | The loop: listen → turn → speak, with interrupt(). |
| `web/js/core/bargein.js` | VAD on the mic while Aloud speaks. Cuts audio in one frame. |
| `web/js/core/keepalive.js` | Wake Lock, Media Session, screen-off survival. |
| `web/js/core/motion.js` | Walking detection and cadence, degrades to nothing. |
| `web/js/views/crunch.js` | The room. The single crunch surface. |
| `web/js/components/heatmap.js` | Hesitation heatmap (uses the `dataviz` skill). |
| `web/manifest.webmanifest`, `web/sw.js` | PWA install + offline shell. |

**Schema additions** (migration in `app/storage/db.py`, `SCHEMA_VERSION` bump)

- `resources.role` — `notes | past_paper | mark_scheme | syllabus | unknown`
- `concepts.exam_weight` (REAL), `concepts.weight_basis` (TEXT: how it was derived)
- `concept_signals` — the learner model, one row per (user, concept)
- `crunch_sessions`, `crunch_turns` — session + deletable transcript
- `cost_log` — tokens in/out, model, cost, per session
- `embedding_cache` — content hash → vector, so re-analysis is free

### The learner model (C) — exact shape

> **Changed during phase C.** The signals below live as columns on the existing
> `mastery` row, not in a separate `concept_signals` table. `mastery` was
> already one row per student per concept; a second table beside it would be
> two answers to "how well do they know this", which is exactly the drift this
> section exists to prevent. `test_only_the_learner_service_writes_the_learner_model`
> enforces the one-writer rule mechanically.

```python
@dataclass
class ConceptSignals:
    user_id: str
    concept_id: str
    correct: int = 0
    wrong: int = 0
    miss_count: int = 0            # asked, no answer offered
    latency_ms: list[int]          # rolling window, last 8
    filler_count: int = 0          # "um", "er", "like", "I think"
    self_corrections: int = 0      # "no wait", "actually", "sorry"
    catches: int = 0               # liar mode: lies caught
    lies_heard: int = 0
    clear_streak: int = 0          # consecutive clears, for quarantine release
    quarantined: bool = False
    last_seen_at: datetime | None = None
```

Derived, in `app/domain/learner.py`, all pure:

- `confidence(s) -> float` — 0..1. Correctness, decayed by recency, penalised by
  hesitation. This is what triage ranks on.
- `hesitation(s) -> float` — 0..1 from median latency against the concept's
  expected answer length, plus fillers and self-corrections per answer.
- `is_shaky(s) -> bool` — right answers, hesitant delivery. Feature 4's whole
  point, and the steering signal for every other mode.
- `should_quarantine(s) -> bool` — `miss_count >= 3`.
- `is_cleared(s) -> bool` — `clear_streak >= 2`.

---

## Phases

Order follows the brief. Each phase ends with: tests green, app running, a
hand-runnable demo script, a code-review pass, a security pass, `PROGRESS.md`
updated.

| Phase | Contents | Why here |
|---|---|---|
| **0** | Repo hygiene: git (done), feature flags, `PROGRESS.md`, Playwright-Python, latency harness | Nothing is measurable without it |
| **A** | Ingestion: roles, OCR, topic graph with mark weightings, embedding cache | Everything downstream reads this |
| **B** | Voice loop: streaming turn, barge-in, push-to-talk, wake lock, media session, PWA | The product *is* this loop |
| **C** | Learner model + `learner_service` | 14 features read it |
| **D** | Orchestrator, mode registry, crunch session UI (frontend-design pass) | The room every mode lives in |
| **1** | Triage mode | The pitch |
| **4** | Hesitation heatmap | Steers everything after it |
| **7** | Examiner voice | The pitch |
| **12, 5, 6, 10, 3, 2** | Panic phrase, cold-call, teach-back, liar, sleep-boundary, last 90s | Modes on a working spine |
| **8, 9, 15, 11, 13, 14** | Pattern hunter, bridge questions, quarantine, paper handshake, walk mode, memory palace | Breadth |
| **16** | Head-to-head duels | Multi-user, flagged off, last |

---

## Definition of done, per feature

Every one of these also requires: unit tests for logic, an integration test
through the real voice loop, a Playwright end-to-end for anything with UI,
captions, keyboard reachability, and an entry in `PROGRESS.md`.

**A · Ingestion.** PDF, DOCX, images-with-OCR and pasted text all import and are
tagged `notes | past_paper | mark_scheme | syllabus`. A topic graph exists with
a mark weighting per topic and a stated basis for each. *Done when:* uploading
a syllabus plus two past papers produces weightings whose basis is inspectable,
and an un-OCR-able image fails with a message that says what to do, not "error".

**B · Voice loop.** Streaming STT → LLM → TTS. Barge-in stops Aloud inside one
animation frame. Push-to-talk works when VAD does not. The session survives the
screen turning off. *Done when:* `tools/latency.py` reports p50 under 1.2s to
first audio over 20 turns, and a manual test with the phone in a pocket keeps
teaching for 5 minutes with the screen off.

**C · Learner model.** One table, one writer, every field in the brief present.
*Done when:* a single spoken answer updates correctness, latency, fillers and
self-corrections in one transaction, and two modes disagree about nothing
because they read the same row.

**D · Orchestrator.** Modes are pluggable strategies over one audio path.
*Done when:* adding a mode requires one file and one registry line, and no mode
owns a socket, a recogniser or a speech queue.

**1 · Triage.** Takes exam date and time, computes hours left, ranks by marks ×
weakness, time-boxes a plan, and refuses low-value material *out loud with the
reason*. *Done when:* given 6 hours and 14 topics it skips at least one and the
spoken reason names the marks at stake.

**2 · Last 90 seconds.** Spoken recap of formulas, definitions and traps,
restricted to concepts where `confidence >= CONFIDENT_AT`. *Done when:* a
concept below the threshold provably never appears, and the whole thing fits in
90 seconds at the learner's speed.

**3 · Sleep boundary.** Quiet, slow, longer pauses, no quizzing. *Done when:*
rate and pause length are measurably lower than any other mode and no turn
expects an answer.

**4 · Hesitation heatmap.** Flags shaky topics that were answered *correctly*.
*Done when:* a seeded session of slow, filler-heavy correct answers turns a
topic amber, the heatmap renders it, and triage reorders because of it.

**5 · Cold-call interrupts.** Cuts in mid-explanation at unpredictable moments,
frequency adjustable. *Done when:* the interval is random within bounds, the
setting changes it, and the interrupt stops Aloud's own speech cleanly.

**6 · Teach-back.** Aloud plays a curious younger student; the learner explains;
Aloud scores against source material and names specific gaps. *Done when:* the
gaps it names are quotable section ids, and with no API key the mode is listed
as unavailable rather than faked.

**7 · Examiner voice.** Grounded strictly in an uploaded mark scheme. Marks
awarded and lost, with reasons, plus a command-word drill. *Done when:* with no
mark scheme uploaded it says so plainly and refuses, and every mark awarded
cites a mark-scheme line.

**8 · Pattern hunter.** Recurring question shapes and topics across past papers,
presented as likelihood. *Done when:* output always carries a frequency
("4 of the last 6 papers"), never a promise, and a single paper produces
"not enough papers to see a pattern".

**9 · Bridge questions.** Questions connecting two topics. *Done when:* both
topics are cited and the question fails generation rather than inventing a link
that the material does not support.

**10 · Liar mode.** Plausible falsehoods; catch rate tracked per concept.
*Done when:* the lie is always a mutation of a grounded fact, the truth is
stated immediately after either way, and `catches/lies_heard` lands in the
learner model.

**11 · Paper handshake.** Aloud sets a sketch or problem, the learner photographs
it, Aloud critiques aloud using vision. *Done when:* the photo is never stored
after the turn, and without vision the mode is unavailable and says why.

**12 · Panic phrase.** "I blanked" → under one second to a 15-second retrieval
chain from a known nearby concept. *Done when:* the phrase is matched before the
turn reaches the model, the chain starts from a concept with high confidence,
and the measured response is under one second.

**13 · Walk mode.** Motion-derived pace times the prompts. *Done when:* with no
motion sensor the mode still runs on a fixed cadence and says nothing about
sensors.

**14 · Memory palace.** Learner describes a route; Aloud places facts along it,
narrates, then retests by walking it again. *Done when:* the same route replays
in the same order across sessions.

**15 · Quarantine.** Three misses pulls a concept into a drill queue until it
clears twice in a row. *Done when:* entry and release are driven only by the
learner model, and the queue survives a restart.

**16 · Duels.** Two learners on the same exam quiz each other asynchronously by
voice, behind a flag, default off. *Done when:* the flag off leaves no route,
no table growth and no UI.

---

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Aloud hears its own voice and barges in on itself** | Kills the product | `echoCancellation: true`, gate the VAD on the synth's `speaking` flag, require 180ms above threshold, and calibrate against ambient during the first second. Test on speaker, not just headphones. |
| **1.2s budget missed** | High | Measure from day one (`tools/latency.py`). Speak the first sentence as soon as it arrives rather than waiting for the turn. Trim retrieval to top-k=4. |
| **iOS suspends audio with the screen off** | High | Media Session + a looping silent track; document honestly in the README if a given iOS version still suspends. Do not claim it works untested. |
| **Model invents marks or patterns** | Reputational | Every mark cites a mark-scheme line id; pattern output is frequency-only; a grounding check rejects turns with no section id and says "your material does not cover this". |
| **No OCR binary installed** | Medium | Claude vision fallback, then an honest failure that names the install command. |
| **Cost runs away in a 6-hour crunch** | Medium | Per-turn token cap, embedding cache, cost logged per session and shown in settings. |
| **SQLite write contention once duels exist** | Medium | Already serialised behind one writer lock; duels are async turn-based, not realtime. |
| **Whisper's first load stalls the first answer** | Medium | Warm the model on session start, in the background, not on the first answer. |
| **Refocusing deletes UI the user liked** | Medium | The baseline is committed (`f1c6fb3`); anything deleted is one `git revert` away. |

---

## Testing strategy

- **Unit** — pure domain maths: `learner.py`, `triage.py`, mode strategies.
  Fast, no I/O, the bulk of the suite.
- **Integration** — the voice loop end to end with fake STT/TTS providers:
  transcript in, turn out, learner model written. This is where the seams that
  unit tests miss actually break.
- **End-to-end** — Playwright for Python against a real server: upload, start a
  crunch, answer, interrupt, see the heatmap. Plus the existing
  `tools/journey.py` and `tools/qa.py` sweeps.
- **Latency** — `tools/latency.py`, a budget test that fails the build.
- **Accessibility** — keyboard-only pass and caption presence asserted in e2e.

---

## What I need from you

Three optional installs, none blocking:

```bash
claude mcp add --transport http context7 https://mcp.context7.com/mcp
python -m pip install playwright pytest-playwright && python -m playwright install chromium
winget install --id UB-Mannheim.TesseractOCR -e && python -m pip install pytesseract
```

And an `ANTHROPIC_API_KEY` in `.env` when you want modes 6, 7, 10 and 11 at full
quality. Everything else runs without one.
