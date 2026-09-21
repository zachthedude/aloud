"""SQLite access.

One connection per operation, WAL enabled so reads never block the background
analysis writer, and foreign keys enforced so a deleted space really does take
its lessons, audio and progress with it.

The schema is created on boot and versioned with `user_version`, so an existing
database upgrades in place rather than needing to be thrown away.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL DEFAULT '',
    password_hash   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    preferred_speed REAL NOT NULL DEFAULT 1.0,
    preferred_voice TEXT NOT NULL DEFAULT '',
    explanation_style TEXT NOT NULL DEFAULT 'balanced',
    onboarded       INTEGER NOT NULL DEFAULT 0,
    muted_modes     TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_user ON auth_sessions(user_id);

CREATE TABLE IF NOT EXISTS spaces (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    level       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'empty',
    accent      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spaces_user ON spaces(user_id);

CREATE TABLE IF NOT EXISTS resources (
    id                TEXT PRIMARY KEY,
    space_id          TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    kind              TEXT NOT NULL,
    role              TEXT NOT NULL DEFAULT 'notes',
    original_filename TEXT NOT NULL DEFAULT '',
    stored_path       TEXT NOT NULL DEFAULT '',
    source_url        TEXT NOT NULL DEFAULT '',
    byte_size         INTEGER NOT NULL DEFAULT 0,
    page_count        INTEGER NOT NULL DEFAULT 0,
    section_count     INTEGER NOT NULL DEFAULT 0,
    word_count        INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'queued',
    error             TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resources_space ON resources(space_id);

CREATE TABLE IF NOT EXISTS resource_sections (
    id          TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    space_id    TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL DEFAULT 0,
    heading     TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL,
    page        INTEGER NOT NULL DEFAULT 0,
    word_count  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sections_space ON resource_sections(space_id);
CREATE INDEX IF NOT EXISTS idx_sections_resource ON resource_sections(resource_id);

CREATE TABLE IF NOT EXISTS concepts (
    id             TEXT PRIMARY KEY,
    space_id       TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    summary        TEXT NOT NULL DEFAULT '',
    definition     TEXT NOT NULL DEFAULT '',
    stage          TEXT NOT NULL DEFAULT 'core',
    difficulty     INTEGER NOT NULL DEFAULT 2,
    ordinal        INTEGER NOT NULL DEFAULT 0,
    terminology    TEXT NOT NULL DEFAULT '[]',
    formulas       TEXT NOT NULL DEFAULT '[]',
    facts          TEXT NOT NULL DEFAULT '[]',
    examples       TEXT NOT NULL DEFAULT '[]',
    misconceptions TEXT NOT NULL DEFAULT '[]',
    prerequisites  TEXT NOT NULL DEFAULT '[]',
    section_ids    TEXT NOT NULL DEFAULT '[]',
    exam_weight    REAL NOT NULL DEFAULT 0,
    weight_basis   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_concepts_space ON concepts(space_id);

CREATE TABLE IF NOT EXISTS lessons (
    id                TEXT PRIMARY KEY,
    space_id          TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    concept_id        TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    subtitle          TEXT NOT NULL DEFAULT '',
    stage             TEXT NOT NULL DEFAULT 'core',
    ordinal           INTEGER NOT NULL DEFAULT 0,
    estimated_minutes INTEGER NOT NULL DEFAULT 8,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lessons_space ON lessons(space_id);

CREATE TABLE IF NOT EXISTS lesson_sections (
    id                 TEXT PRIMARY KEY,
    lesson_id          TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    ordinal            INTEGER NOT NULL DEFAULT 0,
    kind               TEXT NOT NULL DEFAULT 'explanation',
    heading            TEXT NOT NULL DEFAULT '',
    script             TEXT NOT NULL,
    question_id        TEXT NOT NULL DEFAULT '',
    source_section_ids TEXT NOT NULL DEFAULT '[]',
    estimated_seconds  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lesson_sections ON lesson_sections(lesson_id, ordinal);

CREATE TABLE IF NOT EXISTS questions (
    id                    TEXT PRIMARY KEY,
    space_id              TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    concept_id            TEXT NOT NULL DEFAULT '',
    lesson_id             TEXT NOT NULL DEFAULT '',
    kind                  TEXT NOT NULL DEFAULT 'explanation',
    prompt                TEXT NOT NULL,
    expected_points       TEXT NOT NULL DEFAULT '[]',
    misconception_markers TEXT NOT NULL DEFAULT '[]',
    follow_up             TEXT NOT NULL DEFAULT '',
    difficulty            INTEGER NOT NULL DEFAULT 2
);
CREATE INDEX IF NOT EXISTS idx_questions_lesson ON questions(lesson_id);

CREATE TABLE IF NOT EXISTS audio_assets (
    id                TEXT PRIMARY KEY,
    lesson_section_id TEXT NOT NULL DEFAULT '',
    lesson_id         TEXT NOT NULL DEFAULT '',
    voice             TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'absent',
    file_path         TEXT NOT NULL DEFAULT '',
    mime_type         TEXT NOT NULL DEFAULT 'audio/mpeg',
    byte_size         INTEGER NOT NULL DEFAULT 0,
    duration_seconds  REAL NOT NULL DEFAULT 0,
    error             TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_audio_section_voice
    ON audio_assets(lesson_section_id, voice);
CREATE INDEX IF NOT EXISTS idx_audio_lesson ON audio_assets(lesson_id);

CREATE TABLE IF NOT EXISTS responses (
    id                      TEXT PRIMARY KEY,
    question_id             TEXT NOT NULL DEFAULT '',
    session_id              TEXT NOT NULL DEFAULT '',
    user_id                 TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    concept_id              TEXT NOT NULL DEFAULT '',
    transcript              TEXT NOT NULL DEFAULT '',
    input_mode              TEXT NOT NULL DEFAULT 'voice',
    judgement               TEXT NOT NULL DEFAULT 'unclear',
    confidence              REAL NOT NULL DEFAULT 0,
    matched_points          TEXT NOT NULL DEFAULT '[]',
    missed_points           TEXT NOT NULL DEFAULT '[]',
    detected_misconceptions TEXT NOT NULL DEFAULT '[]',
    teacher_reply           TEXT NOT NULL DEFAULT '',
    created_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_responses_user ON responses(user_id, concept_id);

CREATE TABLE IF NOT EXISTS mastery (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    space_id       TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    concept_id     TEXT NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    state          TEXT NOT NULL DEFAULT 'not_started',
    evidence_count INTEGER NOT NULL DEFAULT 0,
    correct_streak INTEGER NOT NULL DEFAULT 0,
    struggle_count INTEGER NOT NULL DEFAULT 0,
    last_seen_at   TEXT,
    note           TEXT NOT NULL DEFAULT '',
    correct      INTEGER NOT NULL DEFAULT 0,
    partial      INTEGER NOT NULL DEFAULT 0,
    wrong        INTEGER NOT NULL DEFAULT 0,
    missed       INTEGER NOT NULL DEFAULT 0,
    latencies    TEXT NOT NULL DEFAULT '[]',
    fillers      INTEGER NOT NULL DEFAULT 0,
    corrections  INTEGER NOT NULL DEFAULT 0,
    catches      INTEGER NOT NULL DEFAULT 0,
    lies_heard   INTEGER NOT NULL DEFAULT 0,
    strikes      INTEGER NOT NULL DEFAULT 0,
    clear_streak INTEGER NOT NULL DEFAULT 0,
    quarantined  INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mastery_unique ON mastery(user_id, concept_id);
CREATE INDEX IF NOT EXISTS idx_mastery_space ON mastery(user_id, space_id);

CREATE TABLE IF NOT EXISTS learning_sessions (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    space_id            TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    lesson_id           TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    state               TEXT NOT NULL DEFAULT 'idle',
    section_ordinal     INTEGER NOT NULL DEFAULT 0,
    position_seconds    REAL NOT NULL DEFAULT 0,
    playback_speed      REAL NOT NULL DEFAULT 1.0,
    completed           INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    pending_question_id TEXT NOT NULL DEFAULT '',
    reexplain_count     INTEGER NOT NULL DEFAULT 0,
    extension_offered   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON learning_sessions(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_lesson ON learning_sessions(user_id, lesson_id);

CREATE TABLE IF NOT EXISTS teacher_messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL DEFAULT '',
    space_id    TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL DEFAULT 'teacher',
    text        TEXT NOT NULL,
    concept_id  TEXT NOT NULL DEFAULT '',
    grounded_in TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_space ON teacher_messages(user_id, space_id, created_at);

CREATE TABLE IF NOT EXISTS bookmarks (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    space_id         TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    lesson_id        TEXT NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    section_ordinal  INTEGER NOT NULL DEFAULT 0,
    position_seconds REAL NOT NULL DEFAULT 0,
    label            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_user ON bookmarks(user_id, space_id);

CREATE TABLE IF NOT EXISTS assessments (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    space_id       TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    lesson_id      TEXT NOT NULL DEFAULT '',
    concept_id     TEXT NOT NULL DEFAULT '',
    session_id     TEXT NOT NULL DEFAULT '',
    response_ids   TEXT NOT NULL DEFAULT '[]',
    outcome        TEXT NOT NULL DEFAULT 'developing',
    confidence     REAL NOT NULL DEFAULT 0,
    takeaways      TEXT NOT NULL DEFAULT '[]',
    gaps           TEXT NOT NULL DEFAULT '[]',
    recommendation TEXT NOT NULL DEFAULT '',
    next_action    TEXT NOT NULL DEFAULT 'continue',
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assessments_user ON assessments(user_id, space_id, created_at DESC);

CREATE TABLE IF NOT EXISTS crunch_sessions (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    space_id         TEXT NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    mode             TEXT NOT NULL,
    cursor           TEXT NOT NULL DEFAULT '{}',
    ended            INTEGER NOT NULL DEFAULT 0,
    last_say         TEXT NOT NULL DEFAULT '',
    last_expects     TEXT NOT NULL DEFAULT 'nothing',
    last_concept_id  TEXT NOT NULL DEFAULT '',
    last_question_id TEXT NOT NULL DEFAULT '',
    last_grounded_in TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crunch_user ON crunch_sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    space_id   TEXT NOT NULL DEFAULT '',
    user_id    TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'queued',
    step       TEXT NOT NULL DEFAULT '',
    progress   REAL NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT '{}',
    error      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_space ON jobs(space_id, created_at DESC);
"""


def _add_column(connection: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column if it is not already there.

    `CREATE TABLE IF NOT EXISTS` builds new databases but never changes an
    existing one, so a column added to the schema has to be added to databases
    that predate it too. This is idempotent, which is the only property a
    migration this small needs.
    """
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migrate(connection: sqlite3.Connection) -> None:
    """Bring an older database up to SCHEMA_VERSION."""
    # 2: every resource carries a role, so the examiner can refuse to mark
    #    without a mark scheme and the pattern hunter can find the papers.
    _add_column(connection, "resources", "role", "TEXT NOT NULL DEFAULT 'notes'")
    # 3: concepts carry what they are worth in the exam, so triage can rank
    #    by marks times weakness and say why it is skipping something.
    _add_column(connection, "concepts", "exam_weight", "REAL NOT NULL DEFAULT 0")
    _add_column(connection, "concepts", "weight_basis", "TEXT NOT NULL DEFAULT ''")
    # 4: which crunch modes a student has switched off for themselves.
    _add_column(connection, "users", "muted_modes", "TEXT NOT NULL DEFAULT '[]'")
    # 5: the learner model, on the mastery row so there is one answer per
    #    student per concept rather than two tables that drift apart.
    _add_column(connection, "mastery", "correct", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "partial", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "wrong", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "missed", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "latencies", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(connection, "mastery", "fillers", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "corrections", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "catches", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "lies_heard", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "strikes", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "clear_streak", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "mastery", "quarantined", "INTEGER NOT NULL DEFAULT 0")


class Database:
    """Owns the SQLite file and hands out connections."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=15.0, isolation_level=None, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialise(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                _migrate(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def reader(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def writer(self) -> Iterator[sqlite3.Connection]:
        """A serialised write transaction.

        SQLite allows one writer at a time; taking the lock here turns a
        contended write into a short wait instead of a "database is locked"
        error surfacing to a student mid-lesson.
        """
        with self._write_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def reset(self) -> None:
        """Drop everything. Used by tests and by `run.py --reset`."""
        with self.writer() as connection:
            # Newest first: children are created after the tables they point at,
            # and dropping a parent first fails once its children hold rows.
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY rowid DESC"
            ).fetchall()
            for row in tables:
                connection.execute(f"DROP TABLE IF EXISTS {row['name']}")
        self._initialise()


# ---------------------------------------------------------------------------
# Value conversion
# ---------------------------------------------------------------------------


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default if default is not None else []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def from_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
