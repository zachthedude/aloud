# Progress

Updated after every feature. Three columns that matter: what is done, what is
tested, what is known to be rough. See `PLAN.md` for the plan this tracks.

**Baseline commit:** `f1c6fb3` — the self-paced app before the crunch rebuild.
Anything the rebuild deletes is one `git revert` away.

**Now:** 335 tests passing · 34/34 screens clean in light and dark at 1440 and
390 · browser journey 18/18.

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

## Phases B–D — core

Not started. B (voice loop) is the next one that matters, and the biggest.

## Features 1–16

None implemented. All sixteen are visible and honest about it.
