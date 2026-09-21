"""The orchestrator: one voice loop, many modes.

Every crunch mode runs through here. The service owns everything a mode must
not: checking the mode is allowed, loading the learner model, evaluating
answers, writing them to the learner model through `LearnerService`, keeping
the transcript, and persisting the mode's cursor between turns. A mode only
decides what to say next.

That split is the whole point of phase D. It is why a mode is one small file,
why none of them owns any audio, and why the learner model stays one row per
concept no matter how many modes write to it.
"""

from __future__ import annotations

import logging

from ..domain.assessment import AnswerAnalysis
from ..domain.assessment import evaluate as lexical_evaluate
from ..domain.models import Concept, CrunchSession, Question, TeacherMessage, User, new_id, utcnow
from ..domain.turn import Reply, Turn
from .learner_service import LearnerService
from .modes import registry
from .modes.catalogue import BY_KEY as CATALOGUE_BY_KEY
from .modes.catalogue import describe as describe_mode
from .modes.base import ModeContext

logger = logging.getLogger("aloud.crunch")


class CrunchUnavailable(Exception):
    """The mode exists but cannot run for this student right now. Says why."""


class CrunchNotFound(Exception):
    """No such mode, subject or sitting, as far as this student can tell."""


class CrunchService:
    def __init__(self, repos, engine, learner: LearnerService, settings) -> None:
        self.repos = repos
        self.engine = engine
        self.learner = learner
        self.settings = settings

    # -- lifecycle -----------------------------------------------------------

    def start(self, user: User, space_id: str, mode_key: str) -> tuple[CrunchSession, Turn]:
        if self.repos.spaces.get(space_id, user.id) is None:
            raise CrunchNotFound("That subject could not be found.")
        mode = self._allowed(user, mode_key)

        session = CrunchSession(user_id=user.id, space_id=space_id, mode=mode_key)
        context = self._context(session, user)
        turn = mode.open(context)
        return self._settle(session, context, turn), turn

    def reply(self, user: User, session_id: str, reply: Reply) -> tuple[CrunchSession, Turn]:
        session = self.repos.crunch.get(session_id, user.id)
        if session is None:
            raise CrunchNotFound("That sitting could not be found.")
        if session.ended:
            raise CrunchUnavailable("That sitting has finished. Start a new one to carry on.")
        # Checked again on every turn, so switching a mode off, or losing the
        # key it needs, takes effect mid-sitting rather than at the next one.
        mode = self._allowed(user, session.mode)

        if reply.text.strip():
            self._log(session, "student", reply.text, session.last_concept_id)

        context = self._context(session, user)
        turn = mode.respond(context, reply)
        return self._settle(session, context, turn), turn

    def end(self, user: User, session_id: str) -> CrunchSession:
        session = self.repos.crunch.get(session_id, user.id)
        if session is None:
            raise CrunchNotFound("That sitting could not be found.")
        session.ended = True
        return self.repos.crunch.save(session)

    def get(self, user: User, session_id: str) -> CrunchSession:
        session = self.repos.crunch.get(session_id, user.id)
        if session is None:
            raise CrunchNotFound("That sitting could not be found.")
        return session

    # -- the rules a mode never has to think about -------------------------

    def _allowed(self, user: User, mode_key: str):
        if mode_key not in CATALOGUE_BY_KEY:
            raise CrunchNotFound("There is no mode by that name.")
        has_key = bool(self.settings.anthropic_api_key)
        described = describe_mode(
            CATALOGUE_BY_KEY[mode_key],
            built=registry.effective_flags(self.settings.features).get(mode_key, False),
            has_model=has_key,
            has_vision=has_key,
            muted=mode_key in set(user.muted_modes),
        )
        if described["state"] == "coming":
            raise CrunchUnavailable(f"{described['name']} is not built yet.")
        if described["state"] == "off":
            raise CrunchUnavailable(
                f"You switched {described['name']} off. Switch it back on in Ways of working."
            )
        if described["state"] == "blocked":
            raise CrunchUnavailable(f"{described['name']} {described['reasons'][0]}.")
        return registry.MODES[mode_key]

    def _context(self, session: CrunchSession, user: User) -> ModeContext:
        def observe(concept_id: str, analysis: AnswerAnalysis, reply: Reply):
            before = self.learner.current(user.id, session.space_id, concept_id)
            return self.learner.observe_answer(
                before,
                judgement=analysis.judgement,
                confidence=analysis.confidence,
                transcript=reply.text,
                # A typed answer's pause is typing, not thinking.
                latency_ms=reply.latency_ms if reply.input_mode == "voice" else None,
                note=analysis.detected_misconceptions[0] if analysis.detected_misconceptions else "",
            )

        return ModeContext(
            user_id=user.id,
            space_id=session.space_id,
            cursor=session.cursor,
            concepts=self.learner.concepts(user.id, session.space_id),
            questions_for=self.repos.questions.for_concept,
            evaluate=self._evaluate,
            observe=observe,
            now=utcnow(),
            speed=user.preferred_speed or 1.0,
        )

    def _evaluate(self, transcript: str, question: Question, concept: Concept | None) -> AnswerAnalysis:
        try:
            return self.engine.evaluate_answer(transcript, question, concept)
        except Exception:  # noqa: BLE001 - never strand a student mid-answer
            logger.exception("Evaluation failed for question %s", question.id)
            return lexical_evaluate(transcript, question)

    def _settle(self, session: CrunchSession, context: ModeContext, turn: Turn) -> CrunchSession:
        """Persist the mode's cursor and the turn, and keep the transcript."""
        session.cursor = context.cursor
        session.last_say = turn.say
        session.last_expects = turn.expects
        session.last_concept_id = turn.concept_id
        session.last_question_id = turn.question_id
        session.last_grounded_in = list(turn.grounded_in)
        session.ended = turn.done
        saved = self.repos.crunch.save(session)
        self._log(session, "teacher", turn.say, turn.concept_id, list(turn.grounded_in))
        return saved

    def _log(self, session: CrunchSession, role: str, text: str, concept_id: str,
             grounded_in: list[str] | None = None) -> None:
        self.repos.messages.add(
            TeacherMessage(
                id=new_id("msg"),
                session_id=session.id,
                space_id=session.space_id,
                user_id=session.user_id,
                role=role,
                text=text,
                concept_id=concept_id,
                grounded_in=grounded_in or [],
            )
        )

