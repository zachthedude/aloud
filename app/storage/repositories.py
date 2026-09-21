"""Repositories: the only place SQL and domain objects meet.

Services depend on these, never on sqlite3. Every method that reads something
belonging to a user takes the user id and filters on it in SQL, so an
authorisation check cannot be forgotten at a call site.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain.models import (
    Assessment,
    AudioAsset,
    AudioStatus,
    Bookmark,
    Concept,
    Job,
    Judgement,
    LearningSession,
    LearningSpace,
    Lesson,
    LessonSection,
    LessonState,
    Mastery,
    MasteryRecord,
    PlanStage,
    ProcessingStatus,
    Question,
    QuestionKind,
    Resource,
    ResourceKind,
    ResourceRole,
    ResourceSection,
    SectionKind,
    SpaceStatus,
    StudentResponse,
    TeacherMessage,
    User,
    new_id,
    utcnow,
)
from .db import Database, from_iso, from_json, to_iso, to_json


def _enum(enum_class: Any, raw: Any, default: Any) -> Any:
    """Parse a stored enum value, falling back rather than raising.

    A row written by a newer build should not crash an older one.
    """
    try:
        return enum_class(raw)
    except (ValueError, KeyError):
        return default


class Repositories:
    """Every repository, sharing one database handle."""

    def __init__(self, database: Database) -> None:
        self.db = database
        self.users = UserRepository(database)
        self.auth = AuthRepository(database)
        self.spaces = SpaceRepository(database)
        self.resources = ResourceRepository(database)
        self.sections = SectionRepository(database)
        self.concepts = ConceptRepository(database)
        self.lessons = LessonRepository(database)
        self.questions = QuestionRepository(database)
        self.audio = AudioRepository(database)
        self.responses = ResponseRepository(database)
        self.mastery = MasteryRepository(database)
        self.sessions = SessionRepository(database)
        self.messages = MessageRepository(database)
        self.bookmarks = BookmarkRepository(database)
        self.assessments = AssessmentRepository(database)
        self.jobs = JobRepository(database)


class _Base:
    def __init__(self, database: Database) -> None:
        self.db = database


# ---------------------------------------------------------------------------
# Users and authentication
# ---------------------------------------------------------------------------


class UserRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            password_hash=row["password_hash"],
            created_at=from_iso(row["created_at"]) or utcnow(),
            preferred_speed=row["preferred_speed"],
            preferred_voice=row["preferred_voice"],
            explanation_style=row["explanation_style"],
            onboarded=bool(row["onboarded"]),
            muted_modes=from_json(row["muted_modes"]),
        )

    def create(self, user: User) -> User:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO users (id, email, display_name, password_hash, created_at,
                                      preferred_speed, preferred_voice, explanation_style, onboarded)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (user.id, user.email.lower(), user.display_name, user.password_hash,
                 to_iso(user.created_at), user.preferred_speed, user.preferred_voice,
                 user.explanation_style, int(user.onboarded)),
            )
        return user

    def by_email(self, email: str) -> User | None:
        with self.db.reader() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
            ).fetchone()
        return self._row(row) if row else None

    def by_id(self, user_id: str) -> User | None:
        with self.db.reader() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._row(row) if row else None

    def update_preferences(
        self,
        user_id: str,
        *,
        preferred_speed: float | None = None,
        preferred_voice: str | None = None,
        explanation_style: str | None = None,
        display_name: str | None = None,
        onboarded: bool | None = None,
        muted_modes: list[str] | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("preferred_speed", preferred_speed),
            ("preferred_voice", preferred_voice),
            ("explanation_style", explanation_style),
            ("display_name", display_name),
            ("muted_modes", None if muted_modes is None else to_json(muted_modes)),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if onboarded is not None:
            fields.append("onboarded = ?")
            values.append(int(onboarded))
        if not fields:
            return
        values.append(user_id)
        with self.db.writer() as connection:
            connection.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)


class AuthRepository(_Base):
    """Opaque session tokens. No user data is carried in the cookie itself."""

    def create(self, user_id: str, token: str, *, lifetime_days: int = 30) -> datetime:
        now = utcnow()
        expires = now + timedelta(days=lifetime_days)
        with self.db.writer() as connection:
            connection.execute(
                "INSERT INTO auth_sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
                (token, user_id, to_iso(now), to_iso(expires)),
            )
        return expires

    def user_id_for(self, token: str) -> str | None:
        if not token:
            return None
        with self.db.reader() as connection:
            row = connection.execute(
                "SELECT user_id, expires_at FROM auth_sessions WHERE token = ?", (token,)
            ).fetchone()
        if not row:
            return None
        expires = from_iso(row["expires_at"])
        if expires and expires < datetime.now(timezone.utc):
            self.revoke(token)
            return None
        return row["user_id"]

    def revoke(self, token: str) -> None:
        with self.db.writer() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))

    def purge_expired(self) -> int:
        with self.db.writer() as connection:
            cursor = connection.execute(
                "DELETE FROM auth_sessions WHERE expires_at < ?", (to_iso(utcnow()),)
            )
            return cursor.rowcount or 0


# ---------------------------------------------------------------------------
# Spaces and resources
# ---------------------------------------------------------------------------


class SpaceRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> LearningSpace:
        return LearningSpace(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            description=row["description"],
            level=row["level"],
            status=_enum(SpaceStatus, row["status"], SpaceStatus.EMPTY),
            accent=row["accent"],
            created_at=from_iso(row["created_at"]) or utcnow(),
            updated_at=from_iso(row["updated_at"]) or utcnow(),
        )

    def create(self, space: LearningSpace) -> LearningSpace:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO spaces (id, user_id, title, description, level, status, accent,
                                       created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (space.id, space.user_id, space.title, space.description, space.level,
                 space.status.value, space.accent, to_iso(space.created_at), to_iso(space.updated_at)),
            )
        return space

    def list_for_user(self, user_id: str) -> list[LearningSpace]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM spaces WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, space_id: str, user_id: str) -> LearningSpace | None:
        """Always scoped by user: ownership is enforced in the query."""
        with self.db.reader() as connection:
            row = connection.execute(
                "SELECT * FROM spaces WHERE id = ? AND user_id = ?", (space_id, user_id)
            ).fetchone()
        return self._row(row) if row else None

    def update(
        self,
        space_id: str,
        user_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        level: str | None = None,
        status: SpaceStatus | None = None,
    ) -> None:
        fields = ["updated_at = ?"]
        values: list[Any] = [to_iso(utcnow())]
        for column, value in (("title", title), ("description", description), ("level", level)):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if status is not None:
            fields.append("status = ?")
            values.append(status.value)
        values.extend([space_id, user_id])
        with self.db.writer() as connection:
            connection.execute(
                f"UPDATE spaces SET {', '.join(fields)} WHERE id = ? AND user_id = ?", values
            )

    def touch(self, space_id: str) -> None:
        with self.db.writer() as connection:
            connection.execute(
                "UPDATE spaces SET updated_at = ? WHERE id = ?", (to_iso(utcnow()), space_id)
            )

    def delete(self, space_id: str, user_id: str) -> bool:
        with self.db.writer() as connection:
            cursor = connection.execute(
                "DELETE FROM spaces WHERE id = ? AND user_id = ?", (space_id, user_id)
            )
            return bool(cursor.rowcount)


class ResourceRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> Resource:
        return Resource(
            id=row["id"], space_id=row["space_id"], title=row["title"],
            kind=_enum(ResourceKind, row["kind"], ResourceKind.TEXT),
            role=_enum(ResourceRole, row["role"], ResourceRole.NOTES),
            original_filename=row["original_filename"], stored_path=row["stored_path"],
            source_url=row["source_url"], byte_size=row["byte_size"],
            page_count=row["page_count"], section_count=row["section_count"],
            word_count=row["word_count"],
            status=_enum(ProcessingStatus, row["status"], ProcessingStatus.QUEUED),
            error=row["error"], created_at=from_iso(row["created_at"]) or utcnow(),
        )

    def create(self, resource: Resource) -> Resource:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO resources (id, space_id, title, kind, role, original_filename,
                                          stored_path, source_url, byte_size, page_count,
                                          section_count, word_count, status, error, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (resource.id, resource.space_id, resource.title, resource.kind.value,
                 resource.role.value,
                 resource.original_filename, resource.stored_path, resource.source_url,
                 resource.byte_size, resource.page_count, resource.section_count,
                 resource.word_count, resource.status.value, resource.error,
                 to_iso(resource.created_at)),
            )
        return resource

    def set_role(self, resource_id: str, role: ResourceRole) -> None:
        """Correct what a document is. The student always gets the last word."""
        with self.db.writer() as connection:
            connection.execute(
                "UPDATE resources SET role = ? WHERE id = ?", (role.value, resource_id)
            )

    def list_for_space(self, space_id: str) -> list[Resource]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM resources WHERE space_id = ? ORDER BY created_at", (space_id,)
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, resource_id: str) -> Resource | None:
        with self.db.reader() as connection:
            row = connection.execute(
                "SELECT * FROM resources WHERE id = ?", (resource_id,)
            ).fetchone()
        return self._row(row) if row else None

    def update_status(
        self,
        resource_id: str,
        status: ProcessingStatus,
        *,
        error: str = "",
        page_count: int | None = None,
        section_count: int | None = None,
        word_count: int | None = None,
        title: str | None = None,
    ) -> None:
        fields = ["status = ?", "error = ?"]
        values: list[Any] = [status.value, error]
        for column, value in (
            ("page_count", page_count), ("section_count", section_count),
            ("word_count", word_count), ("title", title),
        ):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        values.append(resource_id)
        with self.db.writer() as connection:
            connection.execute(f"UPDATE resources SET {', '.join(fields)} WHERE id = ?", values)

    def delete(self, resource_id: str) -> None:
        with self.db.writer() as connection:
            connection.execute("DELETE FROM resources WHERE id = ?", (resource_id,))


class SectionRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> ResourceSection:
        return ResourceSection(
            id=row["id"], resource_id=row["resource_id"], space_id=row["space_id"],
            ordinal=row["ordinal"], heading=row["heading"], text=row["text"],
            page=row["page"], word_count=row["word_count"],
        )

    def add_many(self, sections: list[ResourceSection]) -> None:
        if not sections:
            return
        with self.db.writer() as connection:
            connection.executemany(
                """INSERT INTO resource_sections
                   (id, resource_id, space_id, ordinal, heading, text, page, word_count)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [(s.id, s.resource_id, s.space_id, s.ordinal, s.heading, s.text, s.page, s.word_count)
                 for s in sections],
            )

    def list_for_space(self, space_id: str) -> list[ResourceSection]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM resource_sections WHERE space_id = ? ORDER BY resource_id, ordinal",
                (space_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def by_ids(self, section_ids: list[str]) -> list[ResourceSection]:
        if not section_ids:
            return []
        placeholders = ",".join("?" * len(section_ids))
        with self.db.reader() as connection:
            rows = connection.execute(
                f"SELECT * FROM resource_sections WHERE id IN ({placeholders})", section_ids
            ).fetchall()
        found = {row["id"]: self._row(row) for row in rows}
        return [found[sid] for sid in section_ids if sid in found]


# ---------------------------------------------------------------------------
# Curriculum
# ---------------------------------------------------------------------------


class ConceptRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> Concept:
        return Concept(
            id=row["id"], space_id=row["space_id"], name=row["name"], summary=row["summary"],
            definition=row["definition"], stage=_enum(PlanStage, row["stage"], PlanStage.CORE),
            difficulty=row["difficulty"], ordinal=row["ordinal"],
            terminology=from_json(row["terminology"]), formulas=from_json(row["formulas"]),
            facts=from_json(row["facts"]), examples=from_json(row["examples"]),
            misconceptions=from_json(row["misconceptions"]),
            prerequisites=from_json(row["prerequisites"]), section_ids=from_json(row["section_ids"]),
            exam_weight=row["exam_weight"], weight_basis=row["weight_basis"],
        )

    def replace_for_space(self, space_id: str, concepts: list[Concept]) -> None:
        """Swap a space's concepts atomically, so a reanalysis is never half-applied."""
        with self.db.writer() as connection:
            connection.execute("DELETE FROM concepts WHERE space_id = ?", (space_id,))
            connection.executemany(
                """INSERT INTO concepts (id, space_id, name, summary, definition, stage, difficulty,
                                         ordinal, terminology, formulas, facts, examples,
                                         misconceptions, prerequisites, section_ids,
                                         exam_weight, weight_basis)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(c.id, space_id, c.name, c.summary, c.definition, c.stage.value, c.difficulty,
                  c.ordinal, to_json(c.terminology), to_json(c.formulas), to_json(c.facts),
                  to_json(c.examples), to_json(c.misconceptions), to_json(c.prerequisites),
                  to_json(c.section_ids), c.exam_weight, c.weight_basis) for c in concepts],
            )

    def list_for_space(self, space_id: str) -> list[Concept]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM concepts WHERE space_id = ? ORDER BY ordinal", (space_id,)
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, concept_id: str) -> Concept | None:
        with self.db.reader() as connection:
            row = connection.execute("SELECT * FROM concepts WHERE id = ?", (concept_id,)).fetchone()
        return self._row(row) if row else None


class LessonRepository(_Base):
    @staticmethod
    def _lesson(row: sqlite3.Row) -> Lesson:
        return Lesson(
            id=row["id"], space_id=row["space_id"], concept_id=row["concept_id"],
            title=row["title"], subtitle=row["subtitle"],
            stage=_enum(PlanStage, row["stage"], PlanStage.CORE), ordinal=row["ordinal"],
            estimated_minutes=row["estimated_minutes"],
            created_at=from_iso(row["created_at"]) or utcnow(),
        )

    @staticmethod
    def _section(row: sqlite3.Row) -> LessonSection:
        return LessonSection(
            id=row["id"], lesson_id=row["lesson_id"], ordinal=row["ordinal"],
            kind=_enum(SectionKind, row["kind"], SectionKind.EXPLANATION),
            heading=row["heading"], script=row["script"], question_id=row["question_id"],
            source_section_ids=from_json(row["source_section_ids"]),
            estimated_seconds=row["estimated_seconds"],
        )

    def create(self, lesson: Lesson, sections: list[LessonSection]) -> Lesson:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO lessons (id, space_id, concept_id, title, subtitle, stage, ordinal,
                                        estimated_minutes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (lesson.id, lesson.space_id, lesson.concept_id, lesson.title, lesson.subtitle,
                 lesson.stage.value, lesson.ordinal, lesson.estimated_minutes,
                 to_iso(lesson.created_at)),
            )
            connection.executemany(
                """INSERT INTO lesson_sections (id, lesson_id, ordinal, kind, heading, script,
                                                question_id, source_section_ids, estimated_seconds)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [(s.id, lesson.id, s.ordinal, s.kind.value, s.heading, s.script, s.question_id,
                  to_json(s.source_section_ids), s.estimated_seconds) for s in sections],
            )
        return lesson

    def delete_for_space(self, space_id: str) -> None:
        with self.db.writer() as connection:
            connection.execute("DELETE FROM lessons WHERE space_id = ?", (space_id,))

    def list_for_space(self, space_id: str) -> list[Lesson]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM lessons WHERE space_id = ? ORDER BY ordinal", (space_id,)
            ).fetchall()
        return [self._lesson(row) for row in rows]

    def get(self, lesson_id: str) -> Lesson | None:
        with self.db.reader() as connection:
            row = connection.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
        return self._lesson(row) if row else None

    def sections(self, lesson_id: str) -> list[LessonSection]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM lesson_sections WHERE lesson_id = ? ORDER BY ordinal", (lesson_id,)
            ).fetchall()
        return [self._section(row) for row in rows]

    def section(self, section_id: str) -> LessonSection | None:
        with self.db.reader() as connection:
            row = connection.execute(
                "SELECT * FROM lesson_sections WHERE id = ?", (section_id,)
            ).fetchone()
        return self._section(row) if row else None


class QuestionRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> Question:
        return Question(
            id=row["id"], space_id=row["space_id"], concept_id=row["concept_id"],
            lesson_id=row["lesson_id"], kind=_enum(QuestionKind, row["kind"], QuestionKind.EXPLANATION),
            prompt=row["prompt"], expected_points=from_json(row["expected_points"]),
            misconception_markers=from_json(row["misconception_markers"]),
            follow_up=row["follow_up"], difficulty=row["difficulty"],
        )

    def add_many(self, questions: list[Question]) -> None:
        if not questions:
            return
        with self.db.writer() as connection:
            connection.executemany(
                """INSERT OR REPLACE INTO questions (id, space_id, concept_id, lesson_id, kind, prompt,
                                          expected_points, misconception_markers, follow_up, difficulty)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [(q.id, q.space_id, q.concept_id, q.lesson_id, q.kind.value, q.prompt,
                  to_json(q.expected_points), to_json(q.misconception_markers), q.follow_up,
                  q.difficulty) for q in questions],
            )

    def get(self, question_id: str) -> Question | None:
        with self.db.reader() as connection:
            row = connection.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        return self._row(row) if row else None

    def for_concept(self, concept_id: str) -> list[Question]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM questions WHERE concept_id = ?", (concept_id,)
            ).fetchall()
        return [self._row(row) for row in rows]

    def delete_for_space(self, space_id: str) -> None:
        with self.db.writer() as connection:
            connection.execute("DELETE FROM questions WHERE space_id = ?", (space_id,))


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------


class AudioRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> AudioAsset:
        return AudioAsset(
            id=row["id"], lesson_section_id=row["lesson_section_id"], lesson_id=row["lesson_id"],
            voice=row["voice"], status=_enum(AudioStatus, row["status"], AudioStatus.ABSENT),
            file_path=row["file_path"], mime_type=row["mime_type"], byte_size=row["byte_size"],
            duration_seconds=row["duration_seconds"], error=row["error"],
            created_at=from_iso(row["created_at"]) or utcnow(),
        )

    def upsert(self, asset: AudioAsset) -> AudioAsset:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO audio_assets (id, lesson_section_id, lesson_id, voice, status,
                                             file_path, mime_type, byte_size, duration_seconds,
                                             error, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(lesson_section_id, voice) DO UPDATE SET
                       status = excluded.status, file_path = excluded.file_path,
                       mime_type = excluded.mime_type, byte_size = excluded.byte_size,
                       duration_seconds = excluded.duration_seconds, error = excluded.error""",
                (asset.id, asset.lesson_section_id, asset.lesson_id, asset.voice,
                 asset.status.value, asset.file_path, asset.mime_type, asset.byte_size,
                 asset.duration_seconds, asset.error, to_iso(asset.created_at)),
            )
        return asset

    def for_lesson(self, lesson_id: str, voice: str) -> list[AudioAsset]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM audio_assets WHERE lesson_id = ? AND voice = ?", (lesson_id, voice)
            ).fetchall()
        return [self._row(row) for row in rows]

    def for_section(self, lesson_section_id: str, voice: str) -> AudioAsset | None:
        with self.db.reader() as connection:
            row = connection.execute(
                "SELECT * FROM audio_assets WHERE lesson_section_id = ? AND voice = ?",
                (lesson_section_id, voice),
            ).fetchone()
        return self._row(row) if row else None

    def get(self, asset_id: str) -> AudioAsset | None:
        with self.db.reader() as connection:
            row = connection.execute("SELECT * FROM audio_assets WHERE id = ?", (asset_id,)).fetchone()
        return self._row(row) if row else None


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


class ResponseRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> StudentResponse:
        return StudentResponse(
            id=row["id"], question_id=row["question_id"], session_id=row["session_id"],
            user_id=row["user_id"], concept_id=row["concept_id"], transcript=row["transcript"],
            input_mode=row["input_mode"], judgement=_enum(Judgement, row["judgement"], Judgement.UNCLEAR),
            confidence=row["confidence"], matched_points=from_json(row["matched_points"]),
            missed_points=from_json(row["missed_points"]),
            detected_misconceptions=from_json(row["detected_misconceptions"]),
            teacher_reply=row["teacher_reply"], created_at=from_iso(row["created_at"]) or utcnow(),
        )

    def create(self, response: StudentResponse) -> StudentResponse:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO responses (id, question_id, session_id, user_id, concept_id, transcript,
                                          input_mode, judgement, confidence, matched_points,
                                          missed_points, detected_misconceptions, teacher_reply,
                                          created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (response.id, response.question_id, response.session_id, response.user_id,
                 response.concept_id, response.transcript, response.input_mode,
                 response.judgement.value, response.confidence, to_json(response.matched_points),
                 to_json(response.missed_points), to_json(response.detected_misconceptions),
                 response.teacher_reply, to_iso(response.created_at)),
            )
        return response

    def for_session(self, session_id: str) -> list[StudentResponse]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM responses WHERE session_id = ? ORDER BY created_at", (session_id,)
            ).fetchall()
        return [self._row(row) for row in rows]

    def recent_for_user(self, user_id: str, limit: int = 40) -> list[StudentResponse]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM responses WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row(row) for row in rows]


class MasteryRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> MasteryRecord:
        return MasteryRecord(
            id=row["id"], user_id=row["user_id"], space_id=row["space_id"],
            concept_id=row["concept_id"], state=_enum(Mastery, row["state"], Mastery.NOT_STARTED),
            evidence_count=row["evidence_count"], correct_streak=row["correct_streak"],
            struggle_count=row["struggle_count"], last_seen_at=from_iso(row["last_seen_at"]),
            note=row["note"],
            correct=row["correct"], partial=row["partial"], wrong=row["wrong"],
            missed=row["missed"], latencies=from_json(row["latencies"]),
            fillers=row["fillers"], corrections=row["corrections"],
            catches=row["catches"], lies_heard=row["lies_heard"],
            strikes=row["strikes"], clear_streak=row["clear_streak"],
            quarantined=bool(row["quarantined"]),
        )

    def get(self, user_id: str, concept_id: str) -> MasteryRecord | None:
        with self.db.reader() as connection:
            row = connection.execute(
                "SELECT * FROM mastery WHERE user_id = ? AND concept_id = ?", (user_id, concept_id)
            ).fetchone()
        return self._row(row) if row else None

    def get_or_create(self, user_id: str, space_id: str, concept_id: str) -> MasteryRecord:
        existing = self.get(user_id, concept_id)
        if existing:
            return existing
        record = MasteryRecord(
            id=new_id("mst"), user_id=user_id, space_id=space_id, concept_id=concept_id
        )
        self.save(record)
        return record

    def save(self, record: MasteryRecord) -> MasteryRecord:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO mastery (id, user_id, space_id, concept_id, state, evidence_count,
                                        correct_streak, struggle_count, last_seen_at, note,
                                        correct, partial, wrong, missed, latencies, fillers, corrections, catches, lies_heard, strikes, clear_streak, quarantined)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, concept_id) DO UPDATE SET
                       state = excluded.state, evidence_count = excluded.evidence_count,
                       correct_streak = excluded.correct_streak,
                       struggle_count = excluded.struggle_count,
                       last_seen_at = excluded.last_seen_at, note = excluded.note,
                       correct = excluded.correct,
                       partial = excluded.partial,
                       wrong = excluded.wrong,
                       missed = excluded.missed,
                       latencies = excluded.latencies,
                       fillers = excluded.fillers,
                       corrections = excluded.corrections,
                       catches = excluded.catches,
                       lies_heard = excluded.lies_heard,
                       strikes = excluded.strikes,
                       clear_streak = excluded.clear_streak,
                       quarantined = excluded.quarantined""",
                (record.id, record.user_id, record.space_id, record.concept_id, record.state.value,
                 record.evidence_count, record.correct_streak, record.struggle_count,
                 to_iso(record.last_seen_at), record.note,
                 record.correct, record.partial, record.wrong, record.missed,
                 to_json(record.latencies), record.fillers, record.corrections,
                 record.catches, record.lies_heard, record.strikes, record.clear_streak,
                 int(record.quarantined)),
            )
        return record

    def for_space(self, user_id: str, space_id: str) -> dict[str, MasteryRecord]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM mastery WHERE user_id = ? AND space_id = ?", (user_id, space_id)
            ).fetchall()
        return {row["concept_id"]: self._row(row) for row in rows}

    def for_user(self, user_id: str) -> list[MasteryRecord]:
        with self.db.reader() as connection:
            rows = connection.execute("SELECT * FROM mastery WHERE user_id = ?", (user_id,)).fetchall()
        return [self._row(row) for row in rows]


    def forget_user(self, user_id: str) -> dict[str, int]:
        """Delete everything Aloud has learned about a student, in one transaction.

        The learner model, every transcript of what they said, every reply,
        and every end-of-lesson assessment. Their material and lessons stay:
        those are what they uploaded, not what we inferred about them.
        """
        tables = ("mastery", "responses", "teacher_messages", "assessments")
        removed: dict[str, int] = {}
        with self.db.writer() as connection:
            for table in tables:
                cursor = connection.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                removed[table] = cursor.rowcount
        return removed


class SessionRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> LearningSession:
        return LearningSession(
            id=row["id"], user_id=row["user_id"], space_id=row["space_id"],
            lesson_id=row["lesson_id"], state=_enum(LessonState, row["state"], LessonState.IDLE),
            section_ordinal=row["section_ordinal"], position_seconds=row["position_seconds"],
            playback_speed=row["playback_speed"], completed=bool(row["completed"]),
            started_at=from_iso(row["started_at"]) or utcnow(),
            updated_at=from_iso(row["updated_at"]) or utcnow(),
            pending_question_id=row["pending_question_id"],
            reexplain_count=row["reexplain_count"], extension_offered=bool(row["extension_offered"]),
        )

    def create(self, session: LearningSession) -> LearningSession:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO learning_sessions (id, user_id, space_id, lesson_id, state,
                        section_ordinal, position_seconds, playback_speed, completed, started_at,
                        updated_at, pending_question_id, reexplain_count, extension_offered)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (session.id, session.user_id, session.space_id, session.lesson_id,
                 session.state.value, session.section_ordinal, session.position_seconds,
                 session.playback_speed, int(session.completed), to_iso(session.started_at),
                 to_iso(session.updated_at), session.pending_question_id,
                 session.reexplain_count, int(session.extension_offered)),
            )
        return session

    def save(self, session: LearningSession) -> LearningSession:
        session.updated_at = utcnow()
        with self.db.writer() as connection:
            connection.execute(
                """UPDATE learning_sessions SET state = ?, section_ordinal = ?, position_seconds = ?,
                        playback_speed = ?, completed = ?, updated_at = ?, pending_question_id = ?,
                        reexplain_count = ?, extension_offered = ?
                   WHERE id = ? AND user_id = ?""",
                (session.state.value, session.section_ordinal, session.position_seconds,
                 session.playback_speed, int(session.completed), to_iso(session.updated_at),
                 session.pending_question_id, session.reexplain_count,
                 int(session.extension_offered), session.id, session.user_id),
            )
        return session

    def get(self, session_id: str, user_id: str) -> LearningSession | None:
        with self.db.reader() as connection:
            row = connection.execute(
                "SELECT * FROM learning_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
            ).fetchone()
        return self._row(row) if row else None

    def latest_for_lesson(self, user_id: str, lesson_id: str) -> LearningSession | None:
        """The sitting to resume, which is the most recent unfinished one."""
        with self.db.reader() as connection:
            row = connection.execute(
                """SELECT * FROM learning_sessions
                   WHERE user_id = ? AND lesson_id = ?
                   ORDER BY completed ASC, updated_at DESC LIMIT 1""",
                (user_id, lesson_id),
            ).fetchone()
        return self._row(row) if row else None

    def most_recent(self, user_id: str, *, unfinished_only: bool = False) -> LearningSession | None:
        clause = "AND completed = 0" if unfinished_only else ""
        with self.db.reader() as connection:
            row = connection.execute(
                f"""SELECT * FROM learning_sessions WHERE user_id = ? {clause}
                    ORDER BY updated_at DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        return self._row(row) if row else None

    def recent(self, user_id: str, limit: int = 8) -> list[LearningSession]:
        with self.db.reader() as connection:
            rows = connection.execute(
                "SELECT * FROM learning_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row(row) for row in rows]


class MessageRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> TeacherMessage:
        return TeacherMessage(
            id=row["id"], session_id=row["session_id"], space_id=row["space_id"],
            user_id=row["user_id"], role=row["role"], text=row["text"],
            concept_id=row["concept_id"], grounded_in=from_json(row["grounded_in"]),
            created_at=from_iso(row["created_at"]) or utcnow(),
        )

    def add(self, message: TeacherMessage) -> TeacherMessage:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO teacher_messages (id, session_id, space_id, user_id, role, text,
                                                 concept_id, grounded_in, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (message.id, message.session_id, message.space_id, message.user_id, message.role,
                 message.text, message.concept_id, to_json(message.grounded_in),
                 to_iso(message.created_at)),
            )
        return message

    def for_space(self, user_id: str, space_id: str, limit: int = 60) -> list[TeacherMessage]:
        with self.db.reader() as connection:
            rows = connection.execute(
                """SELECT * FROM teacher_messages WHERE user_id = ? AND space_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, space_id, limit),
            ).fetchall()
        return [self._row(row) for row in reversed(rows)]


class BookmarkRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> Bookmark:
        return Bookmark(
            id=row["id"], user_id=row["user_id"], space_id=row["space_id"],
            lesson_id=row["lesson_id"], section_ordinal=row["section_ordinal"],
            position_seconds=row["position_seconds"], label=row["label"],
            created_at=from_iso(row["created_at"]) or utcnow(),
        )

    def add(self, bookmark: Bookmark) -> Bookmark:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO bookmarks (id, user_id, space_id, lesson_id, section_ordinal,
                                          position_seconds, label, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (bookmark.id, bookmark.user_id, bookmark.space_id, bookmark.lesson_id,
                 bookmark.section_ordinal, bookmark.position_seconds, bookmark.label,
                 to_iso(bookmark.created_at)),
            )
        return bookmark

    def list_for_user(self, user_id: str, space_id: str = "") -> list[Bookmark]:
        query = "SELECT * FROM bookmarks WHERE user_id = ?"
        params: list[Any] = [user_id]
        if space_id:
            query += " AND space_id = ?"
            params.append(space_id)
        query += " ORDER BY created_at DESC"
        with self.db.reader() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row(row) for row in rows]

    def delete(self, bookmark_id: str, user_id: str) -> bool:
        with self.db.writer() as connection:
            cursor = connection.execute(
                "DELETE FROM bookmarks WHERE id = ? AND user_id = ?", (bookmark_id, user_id)
            )
            return bool(cursor.rowcount)


class AssessmentRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> Assessment:
        return Assessment(
            id=row["id"], user_id=row["user_id"], space_id=row["space_id"],
            lesson_id=row["lesson_id"], concept_id=row["concept_id"], session_id=row["session_id"],
            response_ids=from_json(row["response_ids"]),
            outcome=_enum(Mastery, row["outcome"], Mastery.DEVELOPING), confidence=row["confidence"],
            takeaways=from_json(row["takeaways"]), gaps=from_json(row["gaps"]),
            recommendation=row["recommendation"], next_action=row["next_action"],
            created_at=from_iso(row["created_at"]) or utcnow(),
        )

    def create(self, assessment: Assessment) -> Assessment:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO assessments (id, user_id, space_id, lesson_id, concept_id, session_id,
                                            response_ids, outcome, confidence, takeaways, gaps,
                                            recommendation, next_action, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (assessment.id, assessment.user_id, assessment.space_id, assessment.lesson_id,
                 assessment.concept_id, assessment.session_id, to_json(assessment.response_ids),
                 assessment.outcome.value, assessment.confidence, to_json(assessment.takeaways),
                 to_json(assessment.gaps), assessment.recommendation, assessment.next_action,
                 to_iso(assessment.created_at)),
            )
        return assessment

    def recent(self, user_id: str, space_id: str = "", limit: int = 12) -> list[Assessment]:
        query = "SELECT * FROM assessments WHERE user_id = ?"
        params: list[Any] = [user_id]
        if space_id:
            query += " AND space_id = ?"
            params.append(space_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.db.reader() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row(row) for row in rows]

    def for_session(self, session_id: str) -> Assessment | None:
        with self.db.reader() as connection:
            row = connection.execute(
                "SELECT * FROM assessments WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return self._row(row) if row else None


class JobRepository(_Base):
    @staticmethod
    def _row(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"], space_id=row["space_id"], user_id=row["user_id"], kind=row["kind"],
            status=row["status"], step=row["step"], progress=row["progress"],
            detail=from_json(row["detail"], {}), error=row["error"],
            created_at=from_iso(row["created_at"]) or utcnow(),
            updated_at=from_iso(row["updated_at"]) or utcnow(),
        )

    def create(self, job: Job) -> Job:
        with self.db.writer() as connection:
            connection.execute(
                """INSERT INTO jobs (id, space_id, user_id, kind, status, step, progress, detail,
                                     error, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (job.id, job.space_id, job.user_id, job.kind, job.status, job.step, job.progress,
                 to_json(job.detail), job.error, to_iso(job.created_at), to_iso(job.updated_at)),
            )
        return job

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        step: str | None = None,
        progress: float | None = None,
        error: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        fields = ["updated_at = ?"]
        values: list[Any] = [to_iso(utcnow())]
        for column, value in (("status", status), ("step", step), ("error", error)):
            if value is not None:
                fields.append(f"{column} = ?")
                values.append(value)
        if progress is not None:
            fields.append("progress = ?")
            values.append(max(0.0, min(1.0, progress)))
        if detail is not None:
            fields.append("detail = ?")
            values.append(to_json(detail))
        values.append(job_id)
        with self.db.writer() as connection:
            connection.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)

    def get(self, job_id: str) -> Job | None:
        with self.db.reader() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def latest_for_space(self, space_id: str, kind: str = "") -> Job | None:
        query = "SELECT * FROM jobs WHERE space_id = ?"
        params: list[Any] = [space_id]
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY created_at DESC LIMIT 1"
        with self.db.reader() as connection:
            row = connection.execute(query, params).fetchone()
        return self._row(row) if row else None
