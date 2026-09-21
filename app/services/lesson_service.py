"""The learning loop at runtime.

    TEACH -> ASK -> LISTEN -> EVALUATE -> ADAPT -> REVIEW

Holds the live session, reads answers, decides what the teacher does next, and
records what the student understands. Every state change goes through the state
machine in `app/domain/lesson_state.py`, and every mastery change through
`app/domain/mastery.py`, so the rules live in testable domain code and this
service stays an orchestrator.
"""

from __future__ import annotations

import logging

from ..domain import mastery as mastery_rules
from ..domain.assessment import AnswerAnalysis, combine
from ..domain.lesson_state import advance
from ..domain.models import (
    Assessment,
    Concept,
    Judgement,
    LearningSession,
    Lesson,
    LessonSection,
    LessonState,
    Mastery,
    Question,
    SectionKind,
    StudentResponse,
    TeacherMessage,
    new_id,
    utcnow,
)
from ..domain.voice_commands import Command, Intent
from .curriculum_service import CurriculumService
from .learner_service import LearnerService
from .providers.base import GroundedAnswer, TeachingEngine

logger = logging.getLogger("aloud.lesson")


class LessonNotFound(LookupError):
    pass


class TeacherReply:
    """What the teacher says back, and what the client should do about it."""

    def __init__(
        self,
        text: str,
        *,
        action: str = "continue",
        state: LessonState = LessonState.AI_SPEAKING,
        analysis: AnswerAnalysis | None = None,
        response_id: str = "",
        mastery: Mastery | None = None,
        source_note: str = "",
        source_ids: list[str] | None = None,
        follow_up_question: Question | None = None,
    ) -> None:
        self.text = text
        self.action = action
        self.state = state
        self.analysis = analysis
        self.response_id = response_id
        self.mastery = mastery
        self.source_note = source_note
        self.source_ids = source_ids or []
        self.follow_up_question = follow_up_question


class LessonService:
    def __init__(
        self,
        repos,
        engine: TeachingEngine,
        curriculum: CurriculumService,
        learner: LearnerService | None = None,
    ) -> None:
        self.repos = repos
        self.engine = engine
        self.curriculum = curriculum
        self.learner = learner or LearnerService(repos)

    # -- sessions ----------------------------------------------------------

    def start_or_resume(self, user_id: str, lesson_id: str) -> LearningSession:
        """Resume the last unfinished sitting, or begin a new one.

        Resuming is the default because a student who closed the app on the bus
        expects to pick up mid-sentence, not start the lesson again.
        """
        lesson = self.repos.lessons.get(lesson_id)
        if lesson is None:
            raise LessonNotFound("That lesson could not be found.")
        if self.repos.spaces.get(lesson.space_id, user_id) is None:
            raise LessonNotFound("That lesson could not be found.")

        existing = self.repos.sessions.latest_for_lesson(user_id, lesson_id)
        if existing is not None and not existing.completed:
            return existing

        user = self.repos.users.by_id(user_id)
        session = LearningSession(
            id=new_id("ses"),
            user_id=user_id,
            space_id=lesson.space_id,
            lesson_id=lesson_id,
            state=LessonState.IDLE,
            playback_speed=user.preferred_speed if user else 1.0,
        )
        return self.repos.sessions.create(session)

    def update_playback(
        self,
        session: LearningSession,
        *,
        state: LessonState | None = None,
        section_ordinal: int | None = None,
        position_seconds: float | None = None,
        playback_speed: float | None = None,
    ) -> LearningSession:
        """Persist playback position so the lesson resumes exactly where it was."""
        if state is not None:
            session.state = advance(session.state, state)
        if section_ordinal is not None:
            session.section_ordinal = max(0, section_ordinal)
        if position_seconds is not None:
            session.position_seconds = max(0.0, position_seconds)
        if playback_speed is not None:
            session.playback_speed = max(0.5, min(3.0, playback_speed))
            # Speed is a durable preference, not a per-lesson setting.
            self.repos.users.update_preferences(
                session.user_id, preferred_speed=session.playback_speed
            )
        return self.repos.sessions.save(session)

    # -- answering ---------------------------------------------------------

    def submit_answer(
        self,
        session: LearningSession,
        question_id: str,
        transcript: str,
        *,
        input_mode: str = "voice",
        latency_ms: int | None = None,
    ) -> TeacherReply:
        """Read an answer, respond in the teacher's voice, and adapt."""
        question = self.repos.questions.get(question_id)
        if question is None:
            raise LessonNotFound("That question is no longer available.")
        concept = self.repos.concepts.get(question.concept_id)

        session.state = advance(session.state, LessonState.EVALUATING)
        self.repos.sessions.save(session)

        try:
            analysis = self.engine.evaluate_answer(transcript, question, concept)
        except Exception:  # noqa: BLE001 - never strand a student mid-answer
            logger.exception("Evaluation failed for question %s", question_id)
            from ..domain.assessment import evaluate as lexical

            analysis = lexical(transcript, question)

        record = self.learner.current(session.user_id, session.space_id, question.concept_id)

        has_prerequisite = bool(concept and concept.prerequisites)
        step = mastery_rules.decide_next_step(
            record,
            analysis.judgement,
            analysis.confidence,
            reexplain_count=session.reexplain_count,
            has_prerequisite=has_prerequisite,
            extension_offered=session.extension_offered,
        )

        reply_text = self._compose_reply(step, analysis, question, concept)

        response = StudentResponse(
            id=new_id("rsp"),
            question_id=question_id,
            session_id=session.id,
            user_id=session.user_id,
            concept_id=question.concept_id,
            transcript=transcript,
            input_mode=input_mode,
            judgement=analysis.judgement,
            confidence=analysis.confidence,
            matched_points=analysis.matched_points,
            missed_points=analysis.missed_points,
            detected_misconceptions=analysis.detected_misconceptions,
            teacher_reply=reply_text,
        )
        self.repos.responses.create(response)

        # One write for the whole answer: correctness, time to answer,
        # fillers and self-corrections land together or not at all. Latency
        # is only meaningful spoken; a typed answer's pause is typing.
        updated = self.learner.observe_answer(
            record,
            judgement=analysis.judgement,
            confidence=analysis.confidence,
            transcript=transcript,
            latency_ms=latency_ms if input_mode == "voice" else None,
            note=analysis.detected_misconceptions[0] if analysis.detected_misconceptions else "",
        )

        self._log(session, "student", transcript, question.concept_id)
        self._log(session, "teacher", reply_text, question.concept_id)

        # Adaptive bookkeeping for this sitting.
        if step.action in ("reexplain", "revisit_prereq"):
            session.reexplain_count += 1
        if step.action == "extend":
            session.extension_offered = True
        session.pending_question_id = ""
        session.state = advance(session.state, LessonState.EXPLAINING)
        self.repos.sessions.save(session)

        follow_up = None
        if step.action == "ask_again" and question.follow_up:
            follow_up = question

        return TeacherReply(
            reply_text,
            action=step.action,
            state=LessonState.EXPLAINING,
            analysis=analysis,
            response_id=response.id,
            mastery=updated.state,
            follow_up_question=follow_up,
        )

    def _compose_reply(
        self,
        step: mastery_rules.NextStep,
        analysis: AnswerAnalysis,
        question: Question,
        concept: Concept | None,
    ) -> str:
        """Turn a judgement into something a teacher would actually say.

        Named gaps are what make this feel like being taught rather than
        marked, so a partial answer is told which piece is missing.
        """
        parts = [step.reason]

        if analysis.judgement is Judgement.CORRECT and analysis.matched_points:
            pass  # The verdict is enough. Piling on praise cheapens it.
        elif analysis.judgement in (Judgement.MOSTLY_CORRECT, Judgement.INCOMPLETE):
            if analysis.matched_points:
                parts.append(f"You had this part: {_trim(analysis.matched_points[0])}")
            if analysis.missed_points:
                parts.append(f"The piece still missing is this: {_trim(analysis.missed_points[0])}")
        elif analysis.judgement is Judgement.MISCONCEPTION and analysis.detected_misconceptions:
            parts.append(f"The trouble is here: {_trim(analysis.detected_misconceptions[0])}")
            if question.expected_points:
                parts.append(f"What is actually going on is this: {_trim(question.expected_points[0])}")
        elif analysis.judgement is Judgement.INCORRECT and question.expected_points:
            parts.append(f"Here is the idea it turns on: {_trim(question.expected_points[0])}")
        elif analysis.judgement is Judgement.UNCLEAR and question.follow_up:
            parts.append(question.follow_up)

        if step.action == "revisit_prereq" and concept and concept.prerequisites:
            prerequisite = self.repos.concepts.get(concept.prerequisites[0])
            if prerequisite:
                parts.append(f"We will go back to {prerequisite.name} first, then return to this.")

        return " ".join(part for part in parts if part).strip()

    # -- asking the teacher ------------------------------------------------

    def ask(
        self,
        user_id: str,
        space_id: str,
        text: str,
        *,
        session: LearningSession | None = None,
        concept_id: str = "",
    ) -> GroundedAnswer:
        """Answer a free-form question, grounded in the student's material."""
        self.curriculum.ensure_indexed(space_id)
        concept = self.repos.concepts.get(concept_id) if concept_id else None
        if concept is None and session is not None:
            lesson = self.repos.lessons.get(session.lesson_id)
            if lesson:
                concept = self.repos.concepts.get(lesson.concept_id)

        sections = self.repos.sections.list_for_space(space_id)
        # Search both the question and the concept, so "why does that happen"
        # still retrieves the right passages when the question has no keywords.
        query = f"{text} {concept.name if concept else ''}".strip()
        found = self.curriculum.retrieval.search(space_id, query, limit=4, sections=sections)
        if not found and concept:
            found = self.curriculum.sections_for_concept(concept, space_id)

        history = [
            (message.role, message.text)
            for message in self.repos.messages.for_space(user_id, space_id, limit=8)
        ]

        try:
            answer = self.engine.answer_question(
                text, concept=concept, sections=found, history=history
            )
        except Exception:  # noqa: BLE001 - a failed answer must not break the lesson
            logger.exception("Teacher answer failed")
            answer = GroundedAnswer(
                text=(
                    "I lost my thread there. Ask me again and I will pick it up, or "
                    "carry on with the lesson and come back to it."
                )
            )

        self._record_message(user_id, space_id, "student", text, concept, session)
        self._record_message(
            user_id, space_id, "teacher", answer.text, concept, session, answer.source_section_ids
        )
        return answer

    def handle_command(
        self,
        user_id: str,
        space_id: str,
        command: Command,
        *,
        session: LearningSession | None = None,
        concept_id: str = "",
    ) -> GroundedAnswer:
        """Act on a voice command that needs the teacher rather than the player."""
        concept = self.repos.concepts.get(concept_id) if concept_id else None
        if concept is None and session is not None:
            lesson = self.repos.lessons.get(session.lesson_id)
            if lesson:
                concept = self.repos.concepts.get(lesson.concept_id)

        if concept is None:
            return self.ask(user_id, space_id, command.text, session=session)

        sections = self.curriculum.sections_for_concept(concept, space_id)
        mode = {
            Intent.EXPLAIN_SIMPLER: "simpler",
            Intent.CONFUSED: "simpler",
            Intent.EXPLAIN_DIFFERENTLY: "different",
            Intent.GIVE_EXAMPLE: "example",
        }.get(command.intent)

        if mode:
            if session is not None:
                session.reexplain_count += 1
                self.repos.sessions.save(session)
            try:
                answer = self.engine.reexplain(concept, sections, mode=mode)
            except Exception:  # noqa: BLE001
                logger.exception("Re-explanation failed")
                answer = GroundedAnswer(
                    text="Let me gather that again. Ask me once more and I will take another run at it."
                )
            self._record_message(
                user_id, space_id, "teacher", answer.text, concept, session, answer.source_section_ids
            )
            return answer

        if command.intent is Intent.DEFINE and command.argument:
            return self.ask(
                user_id, space_id, f"What does {command.argument} mean?",
                session=session, concept_id=concept.id,
            )

        return self.ask(user_id, space_id, command.text, session=session, concept_id=concept.id)

    def next_question(self, concept_id: str, *, exclude: list[str] | None = None) -> Question | None:
        """Another question on a concept, avoiding ones already asked."""
        exclude = exclude or []
        questions = [q for q in self.repos.questions.for_concept(concept_id) if q.id not in exclude]
        return questions[0] if questions else None

    # -- finishing ---------------------------------------------------------

    def complete_lesson(self, session: LearningSession) -> Assessment:
        """Summarise the sitting, set an understanding state, recommend next."""
        lesson = self.repos.lessons.get(session.lesson_id)
        if lesson is None:
            raise LessonNotFound("That lesson could not be found.")
        concept = self.repos.concepts.get(lesson.concept_id)

        responses = self.repos.responses.for_session(session.id)
        analyses = [
            AnswerAnalysis(
                judgement=r.judgement,
                confidence=r.confidence,
                matched_points=r.matched_points,
                missed_points=r.missed_points,
                detected_misconceptions=r.detected_misconceptions,
                coverage=_coverage_of(r),
            )
            for r in responses
        ]
        coverage, confidence = combine(analyses)

        if responses:
            outcome = self.learner.current(session.user_id, session.space_id, lesson.concept_id).state
        else:
            # Listened all the way through without answering anything. That is
            # real exposure, so it counts, but it is not evidence of mastery.
            outcome = self.learner.observe_exposure(
                session.user_id, session.space_id, lesson.concept_id
            ).state

        takeaways = _takeaways(concept)
        gaps = _dedupe(
            [point for r in responses for point in r.missed_points]
            + [m for r in responses for m in r.detected_misconceptions]
        )[:3]

        next_action, recommendation = _recommend(outcome, confidence, bool(gaps))

        assessment = Assessment(
            id=new_id("asm"),
            user_id=session.user_id,
            space_id=session.space_id,
            lesson_id=lesson.id,
            concept_id=lesson.concept_id,
            session_id=session.id,
            response_ids=[r.id for r in responses],
            outcome=outcome,
            confidence=confidence,
            takeaways=takeaways,
            gaps=gaps,
            recommendation=recommendation,
            next_action=next_action,
        )
        self.repos.assessments.create(assessment)

        session.completed = True
        session.state = advance(session.state, LessonState.COMPLETED)
        self.repos.sessions.save(session)
        self.repos.spaces.touch(session.space_id)
        return assessment

    # -- helpers -----------------------------------------------------------

    def lesson_payload(self, lesson: Lesson) -> dict:
        """A lesson and everything needed to play it."""
        sections = self.repos.lessons.sections(lesson.id)
        questions = {
            s.question_id: self.repos.questions.get(s.question_id)
            for s in sections
            if s.question_id
        }
        return {"lesson": lesson, "sections": sections, "questions": questions}

    def _log(self, session: LearningSession, role: str, text: str, concept_id: str) -> None:
        self.repos.messages.add(
            TeacherMessage(
                id=new_id("msg"),
                session_id=session.id,
                space_id=session.space_id,
                user_id=session.user_id,
                role=role,
                text=text,
                concept_id=concept_id,
            )
        )

    def _record_message(
        self,
        user_id: str,
        space_id: str,
        role: str,
        text: str,
        concept: Concept | None,
        session: LearningSession | None,
        grounded_in: list[str] | None = None,
    ) -> None:
        self.repos.messages.add(
            TeacherMessage(
                id=new_id("msg"),
                session_id=session.id if session else "",
                space_id=space_id,
                user_id=user_id,
                role=role,
                text=text,
                concept_id=concept.id if concept else "",
                grounded_in=grounded_in or [],
            )
        )


def _trim(text: str, limit: int = 180) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "..."


def _coverage_of(response: StudentResponse) -> float:
    total = len(response.matched_points) + len(response.missed_points)
    return len(response.matched_points) / total if total else 0.0


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = " ".join(item.lower().split())[:60]
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _takeaways(concept: Concept | None) -> list[str]:
    if concept is None:
        return []
    takeaways: list[str] = []
    if concept.definition:
        takeaways.append(concept.definition)
    takeaways.extend(concept.facts[:2])
    if concept.formulas:
        takeaways.append(f"The relationship to hold onto: {concept.formulas[0]}")
    return [_trim(t, 200) for t in takeaways[:3]]


def _recommend(outcome: Mastery, confidence: float, has_gaps: bool) -> tuple[str, str]:
    """Next action and the sentence describing it. Never a grade, never a push."""
    if outcome is Mastery.MASTERED:
        return "continue", "This one is solid. Move on whenever you want to."
    if outcome is Mastery.UNDERSTOOD:
        if has_gaps:
            return "continue", "You have this. There is a loose end worth picking up sometime."
        return "continue", "You have this. Ready to continue."
    if outcome is Mastery.NEEDS_REVIEW:
        return "review", "This one is worth another pass. Nothing is lost by going again."
    if confidence < 0.4:
        return "review", "I did not get a clear read on this. Another pass would tell us both more."
    return "review", "You are building this. Another explanation should settle it."
