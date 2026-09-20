# Progress

Updated after every feature. Three columns that matter: what is done, what is
tested, what is known to be rough. See `PLAN.md` for the plan this tracks.

**Baseline commit:** `f1c6fb3` — the self-paced app before the crunch rebuild.
Anything the rebuild deletes is one `git revert` away.

---

## Phase 0 — repo hygiene

| Item | State |
|---|---|
| git repository + baseline commit | done |
| Feature flags (`app/features.py`, `ALOUD_FEATURES`) | done |
| Flags exposed to the client on `/api/auth/me` | done |
| `PLAN.md`, `PROGRESS.md` | done |
| Playwright for Python | installed, 1.63.0 with Chromium |
| Context7 MCP | installed and connected |
| Tesseract OCR | **blocked** — its installer needs admin rights and the elevation prompt was refused. See below. |

**Tested:** 8 unit tests for flag resolution, 1 API test that the client is told
which modes exist.

**Rough:** Tesseract could not be installed unattended — winget downloaded and
verified it, then the install failed with `0x800704c7` because the UAC prompt
was not accepted. It needs to be run from an elevated terminal:

    winget install --id UB-Mannheim.TesseractOCR -e
    python -m pip install pytesseract

Until then, OCR falls back to Claude vision when a key is set, and otherwise
says so rather than pretending an image had no text in it.

---

## Phase A — ingestion

### A1 · Resource roles — done

Every upload is tagged `notes`, `past paper`, `mark scheme` or `syllabus`.

- Guessed from the filename on upload, so `9702_s22_ms_12.pdf` arrives tagged.
- Refined once during analysis from the document's opening, so a scan named
  `scan001.pdf` that says MARK SCHEME on its first page corrects itself.
- Never overwrites a role the student set by hand.
- Correctable from the material list: the tag on each card is a real
  `<select>`, so it works by keyboard and screen reader.
- The guess is deliberately timid. `notes` is the default and the other three
  are only claimed on positive evidence, because the examiner must never mark
  against a "mark scheme" that is really a set of revision notes.

**Tested:** 15 unit tests (filename codes, opening-page detection, short codes
not trusted inside prose, only the opening read, database round trip) and 4 API
tests (tagged on upload, correctable, invented roles refused with 422). Full
suite: 299 passing, 32/32 screens clean in light and dark.

**Rough:** the role is not yet used by anything. It exists so that A3
(weightings), 7 (examiner) and 8 (pattern hunter) have something true to read.

### A2 · OCR — next
### A3 · Mark weightings — not started
### A4 · Embedding cache — not started

## Features 1–16

Not started. All sixteen flags default off.
