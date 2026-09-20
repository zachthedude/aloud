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
| Playwright for Python | not installed — command in `PLAN.md` |
| Context7 MCP | not installed — command in `PLAN.md` |
| Tesseract OCR | not installed — command in `PLAN.md` |

**Tested:** 8 unit tests for flag resolution, 1 API test that the client is told
which modes exist. Full suite: 280 passing.

**Rough:** nothing yet. The three uninstalled tools are the user's call, and
each has a documented fallback in `PLAN.md`.

---

## Phases A–D — core

Not started.

## Features 1–16

Not started. All sixteen flags default off.
