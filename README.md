# Aloud

A voice-first learning platform. A student uploads the material they already
study from, and it becomes a teacher that explains each idea out loud, asks
them questions, listens to the answers, and changes course when an answer says
they are not there yet.

The screen is optional. The learning is the product.

```
RESOURCE -> UNDERSTAND -> PLAN -> TEACH -> ASK -> LISTEN -> EVALUATE -> ADAPT -> REVIEW
```

---

## Running it

Python 3.11 or newer. No Node, no build step.

```bash
python -m pip install -r requirements.txt
python run.py --seed
```

Then open <http://127.0.0.1:8000> and sign in as:

```
student@aloud.app  /  learnaloud
```

`--seed` builds a sample Grade 10 Physics subject by running the real
ingestion pipeline over real material, so opening it also proves the pipeline
works. Drop `--seed` to start empty.

| Command | What it does |
| --- | --- |
| `python run.py` | Start the server |
| `python run.py --seed` | Load the demo subject first |
| `python run.py --seed-only` | Load the demo subject and exit |
| `python run.py --reset` | Wipe the database first |
| `python run.py --reload` | Restart on code changes |
| `python -m pytest` | Run the test suite |
| `python tools/qa.py` | Headless browser sweep of every screen |
| `python tools/journey.py` | Walk the whole student journey in a browser |
| `python tools/qa.py --theme dark` | The same sweep in dark mode |

Nothing above needs an API key. See [Providers](#providers) for what changes
when you add one.

### Browser support

Chrome and Edge give the full experience, including in-browser speech
recognition. Firefox and Safari speak lessons and accept typed answers;
spoken answers are recorded and transcribed on the server when a speech
recognition provider is configured, and otherwise the app says so and offers
the text field. Nothing is voice-only.

---

## How it works

### The teaching loop

1. **Ingest.** A file is checked by signature, streamed to disk under a
   generated name, extracted, cleaned of running headers and page numbers, and
   chunked into heading-aware passages. A video or audio recording is
   transcribed first, and split where the speaker paused.
2. **Understand.** Concepts are pulled out of headings, definitions and
   recurring technical terms. Prerequisites are inferred from which concepts
   lean on which others' vocabulary.
3. **Plan.** Concepts are ordered by teaching stage and prerequisite depth. The
   plan is derived on every read, so it reflects current understanding rather
   than a stored checklist.
4. **Teach.** Each concept becomes a lesson of narrated beats: context,
   explanation, example, a question, deeper explanation, recap, next step. The
   shape adapts to the concept - a formula gets a worked beat, a recorded
   misconception gets a warning beat.
5. **Ask and listen.** At check beats the teacher asks a question aloud and the
   floor passes to the student, who answers by voice or by typing.
6. **Evaluate.** The answer is scored against what the question was looking
   for, with an explicit confidence.
7. **Adapt.** A solid answer moves on. A partial one gets another example. A
   misconception gets a different explanation. Repeated trouble steps back to
   the prerequisite underneath.

### What is deliberately absent

There are no streaks, countdowns, daily targets, rankings, speed comparisons,
or anything that can be overdue. Progress is described in words - "You are
building this", "Worth revisiting" - and never as a score. A test asserts that
no teacher reply contains scolding language, and another asserts the dashboard
payload carries no pressure metrics.

Saying "I don't know" is treated as honesty, not as a wrong answer: it is
recorded as unclear, costs no ground, and prompts a different explanation.

---

## Architecture

```
app/
  config.py              Environment configuration
  domain/                Pure rules. No I/O, no framework, no providers.
    models.py              Entities
    lesson_state.py        The conversation state machine
    mastery.py             How evidence moves understanding
    assessment.py          Reading a spoken answer
    curriculum.py          Ordering concepts into a plan
    voice_commands.py      Utterance -> intent
  services/
    providers/             Every outside dependency, behind a Protocol
      base.py                LLMProvider, TeachingEngine, TTS, STT, Embeddings
      ondevice.py            Teaching with no model at all
      model_engine.py        Teaching via a hosted model, with fallback
      anthropic.py           Claude over plain HTTP
      speech.py              gTTS / pyttsx3 / Whisper
      registry.py            The only place a provider is chosen
    documents/             Format readers and the ingestion pipeline
    textanalysis.py        Terms, definitions, formulas, informativeness
    retrieval.py           BM25 over the student's own passages
    curriculum_service.py  resources -> concepts -> lessons
    lesson_service.py      The runtime learning loop
    audio_service.py       Per-section audio rendering and downloads
    jobs.py                Background work with student-facing progress
  storage/                 SQLite schema and repositories
  api/                     Routes, DI container, serialisation
web/                       The client: ES modules, no build step
tests/                     Domain, ingestion, and end-to-end HTTP tests
tools/qa.py                Headless browser QA over DevTools Protocol
```

Dependencies point inward. `domain/` imports nothing from the rest of the app,
which is why its rules can be tested in milliseconds without a database.

### Two ideas worth knowing

**Live narration is the browser's, audio files are the server's.** Lessons are
spoken by the device's own speech engine. That starts instantly, works with no
connection, costs nothing per lesson, and changes speed by re-synthesising
rather than resampling, so the teacher still sounds like a person at 1.75x.
Server-rendered MP3s exist separately, for downloading and keeping.

**The server owns the state machine.** Every transition in a lesson goes
through `domain/lesson_state.py`. An illegal transition is a 409 with the list
of what was allowed, not a silently stuck microphone.

---

## Providers

Everything the app needs from outside is a Protocol in
`services/providers/base.py`, chosen in one function in `registry.py`.

| Interface | Default | Alternative |
| --- | --- | --- |
| `TeachingEngine` | On-device analysis of your files | Claude, via `ANTHROPIC_API_KEY` |
| `TextToSpeechProvider` | gTTS if installed, else the system voice, else off | Any provider implementing the Protocol |
| `SpeechToTextProvider` | Whisper locally if installed, else browser only | Any provider implementing the Protocol |
| Appearance | Follows the device, with a light / dark / system toggle | - |
| `EmbeddingProvider` | Local feature hashing | Any hosted embedding model |

### The on-device teacher

With no API key the product still works, because the default engine genuinely
reads the uploaded document: it finds headings, definitions, formulas, worked
examples and flagged misconceptions, and builds lessons from them.

Its limit is real and worth stating. It **does not invent**. Every sentence it
narrates is drawn from the material or is scaffolding around it. It cannot
produce a fresh analogy for an idea the source never illustrates. That is the
honest reason to add a key.

### Adding Claude

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

`ModelBackedEngine` then writes the lessons, questions and evaluations. It
degrades rather than fails: a timeout, an error, or unparseable JSON falls
through to the on-device engine, so a network blip makes a lesson plainer
instead of breaking it. Model judgements about answers still pass through
`assessment.sanity_check`, so a model confidently marking a two-word answer
correct cannot award mastery nobody earned.

### Adding a provider

Implement the Protocol and return it from `registry.py`. Nothing else changes -
no call site knows which vendor is behind an interface.

---

## Configuration

Copy `.env.example` to `.env`. Every value has a working default, so an empty
file is fine.

| Variable | Default | Notes |
| --- | --- | --- |
| `ALOUD_HOST` / `ALOUD_PORT` | `127.0.0.1` / `8000` | |
| `ALOUD_SECRET_KEY` | random per boot | Set it, or sessions drop on restart |
| `ALOUD_DATA_DIR` | `./data` | Database, uploads and audio |
| `ALOUD_DATABASE_URL` | `sqlite:///./data/aloud.db` | |
| `ALOUD_MAX_UPLOAD_MB` | `25` | Per file |
| `ALOUD_LLM_PROVIDER` | `auto` | `auto` / `ondevice` / `anthropic` |
| `ANTHROPIC_API_KEY` | empty | Enables Claude |
| `ALOUD_TTS_PROVIDER` | `auto` | `auto` / `gtts` / `pyttsx3` / `none` |
| `ALOUD_STT_PROVIDER` | `auto` | `auto` / `whisper` / `none` |
| `ALOUD_DEBUG` | `false` | Also exposes `/api/docs` |

Secrets are read from the environment and never written to disk, logged, or
included in an error message.

---

## Security

- Passwords are hashed with scrypt and a per-user salt; verification is
  constant-time, and a sign-in attempt for an unknown email costs the same as
  one for a known email.
- Sessions are opaque tokens in `HttpOnly`, `SameSite=Lax` cookies. No
  authorisation is ever taken from the request body or a client header.
- Every query for a user's data filters by user id **in SQL**, so a missed
  check is not possible at a call site. Someone else's id returns 404 rather
  than 403, which avoids confirming that the id exists.
- Uploads are checked by file signature rather than by the name the browser
  supplied, capped in size, and stored under a generated filename, which
  removes path traversal as a category instead of sanitising it away.
- URL imports refuse any address that resolves to a private, loopback,
  link-local or reserved range, so the URL field is not a request-forgery
  primitive.
- A strict CSP with no inline script, plus `nosniff`, `DENY` framing, and a
  `Permissions-Policy` granting only the microphone.

`tests/test_api.py::TestAuthorisation` runs a second account against every
endpoint that takes an id.

---

## Testing

```bash
python -m pytest          # 270 tests, about 90 seconds
python -m pytest tests/test_domain.py                  # the rules alone, <1s
python tools/qa.py        # every screen, two widths, real console errors
```

`tools/qa.py` drives Chrome over the DevTools Protocol: it signs in, opens
every screen at desktop and mobile widths, captures screenshots into
`tools/screenshots/`, and **fails the run on any console error or failed
request**. It is the check that catches what a screenshot alone would hide.

`tools/journey.py` walks the journey in the definition of done, through the
real client code: open a lesson, play it, change speed, pause, reach a
question, answer it, get a reply, ask the teacher something, give a voice
command, leave and find the mini player still going, come back to the same
place, finish, and see progress move. It found three real bugs that neither
the unit tests nor the screenshots did, all of them in the seam between the
client's idea of the conversation and the server's.

The API tests drive a real uvicorn server over real HTTP rather than an
in-process test client, so middleware, cookies and error handlers are exercised
as a browser would meet them.

---

## Design

Two ideas hold the interface together.

**The listening room is always deeper than the app around it.** In light mode
that reads as the lights going down; in dark mode as the room closing in.
Either way, starting a lesson changes where you are. Appearance follows the
device by default and the toggle cycles light, dark and system; `js/theme.js`
resolves the choice before first paint, so there is no white flash.

**Colour says who is speaking.** Brass while the teacher talks, mint while it
is the student's turn, a cool neutral while it is thinking. The conversational
state machine is legible at a glance, which is what a product designed to be
used with the screen in a pocket needs.

The one bold element is the **resonance figure**: a Chladni pattern, the shape
sand actually forms on a plate driven at a resonant frequency. Each concept's
id picks a vibration mode, so a lesson is recognisable by its figure, and the
plate rings only while audio is playing. Motion reports state; it is not
decoration.

Type is Newsreader for anything that wants to be read and Figtree for the
interface, both self-hosted so the app works offline and no font request leaves
the machine.

---

## Known limits

- **The on-device scorer is lexical.** It matches vocabulary, not meaning, so a
  correct answer phrased in wording the source never uses can read as
  "mostly correct" rather than "correct". It errs toward the student - the
  reply is still encouraging and costs no ground - and configuring Claude
  replaces it with a semantic judgement.
- **Downloads need a speech provider.** With none configured the UI says
  audio files are switched off rather than showing a button that does nothing.
  Listening in the app is unaffected.
- **Offline playback is partial and honestly labelled.** Lessons play without a
  connection because narration is local, and a downloaded MP3 plays anywhere.
  There is no service worker, so the app shell itself still needs the server.
- **One process, one SQLite file.** Right for a single student or a classroom.
  Multi-tenant deployment wants Postgres and an external job queue; `jobs.py`
  is the single seam for the latter.
- **English only.** The analysis heuristics, synonym table and voice command
  patterns are English-specific, and transcription is pinned to English.
- **A transcript has no headings.** Concepts from a recording come from
  definitions and recurring terms only, so a lecture yields a flatter structure
  than a chapter with headings. Passage boundaries come from the speaker's
  pauses, which is the best signal available without them.
- **Transcription needs the model on disk.** The first use downloads it, and
  that download goes through `huggingface_hub`. In an environment with an old
  pinned `httpx` that import fails, so a cached model is loaded directly from
  its snapshot directory instead and transcription keeps working.
