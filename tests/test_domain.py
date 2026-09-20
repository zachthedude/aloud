"""Domain rules: the state machine, mastery, assessment, commands, ordering.

These are the rules that decide what a student is told and what gets recorded
about them, so they are tested directly rather than only through the API.
"""

from __future__ import annotations

import pytest

from app.domain import curriculum, lesson_state as ls, mastery as ms
from app.domain.assessment import (
    AnswerAnalysis, combine, evaluate, sanity_check, stem, tokenise,
)
from app.domain.models import (
    Concept, Judgement, LessonState as S, Lesson, Mastery, MasteryRecord,
    PlanStage, Question,
)
from app.domain.voice_commands import Intent, normalise, parse


# ---------------------------------------------------------------------------
# The conversation state machine
# ---------------------------------------------------------------------------


class TestLessonState:
    def test_legal_transition_is_allowed(self):
        assert ls.advance(S.PLAYING, S.WAITING_FOR_STUDENT) is S.WAITING_FOR_STUDENT

    def test_illegal_transition_raises(self):
        with pytest.raises(ls.IllegalTransition):
            ls.advance(S.TRANSCRIBING, S.PLAYING)

    def test_reentering_the_same_state_is_allowed(self):
        assert ls.advance(S.LISTENING, S.LISTENING) is S.LISTENING

    def test_error_is_always_recoverable(self):
        """A student must never be stranded by a failure."""
        for target in (S.IDLE, S.PAUSED, S.PLAYING, S.WAITING_FOR_STUDENT):
            assert ls.can_advance(S.ERROR, target)

    @pytest.mark.parametrize("escape", [S.IDLE, S.COMPLETED])
    def test_a_student_can_always_stop(self, escape):
        """No state may trap a session. Closing the app must always work."""
        for state in S:
            if state is escape:
                continue
            assert ls.can_advance(state, escape), f"{state.value} cannot reach {escape.value}"

    def test_no_state_is_a_dead_end(self):
        for state in S:
            assert ls.allowed_from(state), f"{state.value} leads nowhere"

    def test_every_state_can_reach_error(self):
        for state in S:
            if state in (S.ERROR, S.COMPLETED):
                continue
            assert S.ERROR in ls.allowed_from(state), state

    @pytest.mark.parametrize(
        "name,sequence",
        [
            # Pausing happens at any moment, including mid-question. Every one
            # of these is something a student does on a bus.
            ("pause mid-question then answer",
             [S.PLAYING, S.AI_SPEAKING, S.PAUSED, S.WAITING_FOR_STUDENT, S.LISTENING]),
            ("end the lesson from paused", [S.PLAYING, S.PAUSED, S.REVIEWING, S.COMPLETED]),
            ("pause while thinking about an answer",
             [S.PLAYING, S.WAITING_FOR_STUDENT, S.PAUSED, S.EVALUATING]),
            ("come back and resume", [S.IDLE, S.PLAYING, S.PAUSED, S.IDLE, S.PLAYING]),
            ("ask a question mid-lesson", [S.PLAYING, S.THINKING, S.EXPLAINING, S.PLAYING]),
            ("finish after listening only", [S.IDLE, S.PLAYING, S.COMPLETED]),
        ],
    )
    def test_realistic_sittings_are_legal(self, name, sequence):
        state = S.IDLE
        for target in sequence:
            assert ls.can_advance(state, target), f"{name}: {state.value} -> {target.value}"
            state = ls.advance(state, target)

    def test_a_full_question_cycle_is_legal(self):
        state = S.IDLE
        for step in (
            S.PLAYING, S.AI_SPEAKING, S.WAITING_FOR_STUDENT, S.LISTENING,
            S.TRANSCRIBING, S.EVALUATING, S.EXPLAINING, S.PLAYING, S.COMPLETED,
        ):
            state = ls.advance(state, step)
        assert state is S.COMPLETED

    @pytest.mark.parametrize(
        "state,holder",
        [
            (S.PLAYING, "teacher"),
            (S.AI_SPEAKING, "teacher"),
            (S.WAITING_FOR_STUDENT, "student"),
            (S.LISTENING, "student"),
            (S.THINKING, "system"),
        ],
    )
    def test_presentation_says_who_holds_the_floor(self, state, holder):
        assert ls.presentation(state).holder == holder

    def test_microphone_is_only_live_on_the_student_turn(self):
        for state in S:
            presentation = ls.presentation(state)
            if presentation.mic_active:
                assert state in ls.STUDENT_TURN


# ---------------------------------------------------------------------------
# Mastery
# ---------------------------------------------------------------------------


class TestMastery:
    def test_first_correct_answer_starts_developing_not_mastered(self):
        record = ms.apply_evidence(MasteryRecord(), Judgement.CORRECT, 0.9)
        assert record.state is Mastery.DEVELOPING

    def test_solid_requires_two_correct_in_a_row(self):
        record = MasteryRecord()
        for _ in range(4):
            record = ms.apply_evidence(record, Judgement.CORRECT, 0.9)
        assert record.state is Mastery.MASTERED
        assert record.correct_streak == 4

    def test_low_confidence_does_not_move_mastery(self):
        before = MasteryRecord(state=Mastery.DEVELOPING)
        after = ms.apply_evidence(before, Judgement.INCORRECT, 0.2)
        assert after.state is Mastery.DEVELOPING
        assert after.evidence_count == 1, "the attempt is still recorded"

    def test_unclear_never_costs_ground(self):
        before = MasteryRecord(state=Mastery.UNDERSTOOD)
        after = ms.apply_evidence(before, Judgement.UNCLEAR, 0.9)
        assert after.state is Mastery.UNDERSTOOD

    def test_mistakes_never_drop_back_to_not_started(self):
        record = MasteryRecord(state=Mastery.DEVELOPING)
        for _ in range(6):
            record = ms.apply_evidence(record, Judgement.INCORRECT, 0.9)
        assert record.state is Mastery.NEEDS_REVIEW
        assert record.state.rank >= Mastery.NEEDS_REVIEW.rank

    def test_repeated_trouble_parks_the_concept_for_review(self):
        record = MasteryRecord(state=Mastery.UNDERSTOOD)
        record = ms.apply_evidence(record, Judgement.MISCONCEPTION, 0.8)
        record = ms.apply_evidence(record, Judgement.MISCONCEPTION, 0.8)
        assert record.state is Mastery.NEEDS_REVIEW
        assert record.struggle_count == 2

    def test_input_record_is_never_mutated(self):
        before = MasteryRecord(state=Mastery.DEVELOPING, evidence_count=3)
        ms.apply_evidence(before, Judgement.CORRECT, 0.9)
        assert before.evidence_count == 3
        assert before.state is Mastery.DEVELOPING

    def test_unsure_evaluation_asks_again_rather_than_judging(self):
        step = ms.decide_next_step(MasteryRecord(), Judgement.CORRECT, 0.1)
        assert step.action == "ask_again"

    def test_repeated_failure_with_a_prerequisite_steps_back(self):
        record = MasteryRecord(state=Mastery.NEEDS_REVIEW, struggle_count=2)
        step = ms.decide_next_step(
            record, Judgement.INCORRECT, 0.9, has_prerequisite=True
        )
        assert step.action == "revisit_prereq"

    def test_repeated_failure_without_a_prerequisite_re_explains(self):
        record = MasteryRecord(state=Mastery.NEEDS_REVIEW, struggle_count=3)
        step = ms.decide_next_step(
            record, Judgement.INCORRECT, 0.9, has_prerequisite=False
        )
        assert step.action == "reexplain"

    def test_an_extension_is_offered_only_once(self):
        record = MasteryRecord(state=Mastery.MASTERED)
        assert ms.decide_next_step(record, Judgement.CORRECT, 0.9).action == "extend"
        repeat = ms.decide_next_step(
            record, Judgement.CORRECT, 0.9, extension_offered=True
        )
        assert repeat.action == "continue"

    def test_no_teacher_reply_ever_scolds(self):
        forbidden = ("wrong", "fail", "bad", "should have", "must", "behind")
        for judgement in Judgement:
            for confidence in (0.2, 0.6, 0.95):
                text = ms.decide_next_step(MasteryRecord(), judgement, confidence).reason
                assert not any(word in text.lower() for word in forbidden), text

    def test_progress_is_zero_with_no_records(self):
        assert ms.overall_progress([]) == 0.0

    def test_progress_is_one_when_everything_is_solid(self):
        records = [MasteryRecord(state=Mastery.MASTERED) for _ in range(3)]
        assert ms.overall_progress(records) == 1.0


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------


def question(**kwargs) -> Question:
    defaults = {
        "prompt": "Why does acceleration depend on mass?",
        "expected_points": [
            "doubling the force doubles the acceleration",
            "doubling the mass halves the acceleration",
        ],
        "misconception_markers": ["heavier objects always fall faster"],
        "follow_up": "What would change if the mass doubled?",
    }
    defaults.update(kwargs)
    return Question(**defaults)


class TestAssessment:
    def test_a_full_answer_is_correct(self):
        result = evaluate(
            "if you double the force you double the acceleration, "
            "and doubling the mass halves it",
            question(),
        )
        assert result.judgement is Judgement.CORRECT
        assert result.confidence > 0.6

    def test_a_partial_answer_is_not_marked_wrong(self):
        result = evaluate("acceleration depends on the force and the mass", question())
        assert result.judgement in (Judgement.MOSTLY_CORRECT, Judgement.INCOMPLETE)

    def test_an_off_topic_answer_is_incorrect(self):
        result = evaluate("I think it is about magnets and electricity", question())
        assert result.judgement is Judgement.INCORRECT

    def test_a_known_misconception_is_detected(self):
        result = evaluate("heavier objects always fall faster than light ones", question())
        assert result.judgement is Judgement.MISCONCEPTION
        assert result.detected_misconceptions

    def test_saying_you_do_not_know_is_unclear_not_wrong(self):
        """Honesty must cost nothing: unclear leaves mastery untouched."""
        result = evaluate("I don't know", question())
        assert result.judgement is Judgement.UNCLEAR
        record = ms.apply_evidence(
            MasteryRecord(state=Mastery.UNDERSTOOD), result.judgement, result.confidence
        )
        assert record.state is Mastery.UNDERSTOOD

    def test_empty_answers_are_unclear_with_no_confidence(self):
        result = evaluate("", question())
        assert result.judgement is Judgement.UNCLEAR
        assert result.confidence == 0.0

    def test_a_two_word_answer_cannot_be_confident(self):
        result = evaluate("the force", question())
        assert result.judgement is Judgement.UNCLEAR
        assert result.confidence <= 0.4

    def test_synonyms_from_speech_are_accepted(self):
        """A student saying "push" for "force" has not made a mistake."""
        assert stem("push") == stem("force")
        assert stem("heavier") == stem("mass")

    def test_paraphrase_scores_on_pooled_vocabulary(self):
        result = evaluate(
            "a bigger force gives a bigger acceleration but more mass gives less",
            question(),
        )
        assert result.coverage > 0.3

    def test_sanity_check_caps_an_overconfident_model(self):
        """A model marking a two-word answer correct must not award mastery."""
        analysis = AnswerAnalysis(Judgement.CORRECT, 0.99)
        checked = sanity_check(analysis, "yes it is")
        assert checked.judgement is Judgement.UNCLEAR
        assert checked.confidence <= 0.35

    def test_sanity_check_leaves_a_real_answer_alone(self):
        analysis = AnswerAnalysis(Judgement.CORRECT, 0.9)
        checked = sanity_check(analysis, "because acceleration is inversely proportional to mass")
        assert checked.judgement is Judgement.CORRECT

    def test_combining_no_answers_is_zero(self):
        assert combine([]) == (0.0, 0.0)

    def test_tokenise_drops_filler(self):
        assert "um" not in tokenise("um the force is bigger")


# ---------------------------------------------------------------------------
# Voice commands
# ---------------------------------------------------------------------------


class TestVoiceCommands:
    @pytest.mark.parametrize(
        "utterance,intent",
        [
            ("pause", Intent.PAUSE),
            ("Pause.", Intent.PAUSE),
            ("keep going", Intent.RESUME),
            ("say that again", Intent.REPEAT),
            ("go back", Intent.BACK),
            ("skip ahead", Intent.FORWARD),
            ("slow down", Intent.SLOWER),
            ("speed up", Intent.FASTER),
            ("can you explain that more simply", Intent.EXPLAIN_SIMPLER),
            ("explain that another way", Intent.EXPLAIN_DIFFERENTLY),
            ("give me an example", Intent.GIVE_EXAMPLE),
            ("ask me a question", Intent.ASK_ME),
            ("quiz me on everything we just learned", Intent.QUIZ_ME),
            ("i'm confused", Intent.CONFUSED),
            ("move on", Intent.MOVE_ON),
            ("review this later", Intent.REVIEW_LATER),
        ],
    )
    def test_commands_are_recognised(self, utterance, intent):
        assert parse(utterance).intent is intent

    def test_a_definition_request_carries_its_term(self):
        command = parse("what does inertia mean")
        assert command.intent is Intent.DEFINE
        assert command.argument == "inertia"

    def test_a_real_question_is_not_a_command(self):
        assert parse("why does the pressure increase here").intent is Intent.QUESTION

    def test_an_answer_is_not_mistaken_for_a_command(self):
        """A sentence starting with a command word is still an answer."""
        command = parse("stop the motion continues unless a force acts", awaiting_answer=True)
        assert command.intent is Intent.ANSWER

    def test_a_bare_command_still_works_mid_answer(self):
        """Something has gone wrong and the student needs out."""
        assert parse("pause", awaiting_answer=True).intent is Intent.PAUSE
        assert parse("i'm confused", awaiting_answer=True).intent is Intent.CONFUSED

    def test_skipping_is_not_honoured_mid_answer(self):
        """Cutting a student off mid-sentence is worse than missing a command."""
        assert parse("next", awaiting_answer=True).intent is Intent.ANSWER

    def test_filler_is_stripped_before_matching(self):
        assert normalise("um, so, pause") == "pause"
        assert parse("um so pause").intent is Intent.PAUSE

    def test_an_empty_utterance_is_handled(self):
        assert parse("", awaiting_answer=True).intent is Intent.ANSWER
        assert parse("   ").confidence == 0.0


# ---------------------------------------------------------------------------
# Curriculum ordering
# ---------------------------------------------------------------------------


def make_concepts():
    a = Concept(id="a", name="Force", stage=PlanStage.FOUNDATIONS, ordinal=0)
    b = Concept(id="b", name="Second Law", stage=PlanStage.CORE, ordinal=1, prerequisites=["a"])
    c = Concept(id="c", name="Momentum", stage=PlanStage.CORE, ordinal=2, prerequisites=["b"])
    return [a, b, c]


class TestCurriculum:
    def test_prerequisite_depth_is_computed(self):
        depth = curriculum.prerequisite_depth(make_concepts())
        assert depth == {"a": 0, "b": 1, "c": 2}

    def test_ordering_respects_prerequisites(self):
        shuffled = list(reversed(make_concepts()))
        assert [c.name for c in curriculum.order_concepts(shuffled)] == [
            "Force", "Second Law", "Momentum",
        ]

    def test_a_prerequisite_loop_degrades_instead_of_crashing(self):
        a = Concept(id="a", name="A", ordinal=0, prerequisites=["b"])
        b = Concept(id="b", name="B", ordinal=1, prerequisites=["a"])
        with pytest.raises(curriculum.CircularPrerequisite):
            curriculum.prerequisite_depth([a, b])
        # Ordering must still return something usable.
        assert len(curriculum.order_concepts([a, b])) == 2

    def test_unknown_prerequisites_are_ignored(self):
        concept = Concept(id="a", name="A", prerequisites=["does-not-exist"])
        assert curriculum.prerequisite_depth([concept]) == {"a": 0}

    def test_exactly_one_item_is_suggested_next(self):
        concepts = make_concepts()
        lessons = [Lesson(id=f"l{c.id}", concept_id=c.id, title=c.name) for c in concepts]
        plan = curriculum.build_plan(concepts, lessons, {})
        suggested = [item for item in plan if item.recommendation == "Suggested next"]
        assert len(suggested) == 1
        assert suggested[0].title == "Force", "the one with no prerequisites"

    def test_later_lessons_are_marked_as_building_on_earlier_ones(self):
        concepts = make_concepts()
        lessons = [Lesson(id=f"l{c.id}", concept_id=c.id, title=c.name) for c in concepts]
        plan = curriculum.build_plan(concepts, lessons, {})
        assert plan[1].prerequisites_met is False

    def test_review_is_recommended_before_new_material(self):
        concepts = make_concepts()
        lessons = [Lesson(id=f"l{c.id}", concept_id=c.id, title=c.name) for c in concepts]
        mastery = {
            "a": MasteryRecord(concept_id="a", state=Mastery.NEEDS_REVIEW),
            "b": MasteryRecord(concept_id="b", state=Mastery.UNDERSTOOD),
        }
        plan = curriculum.build_plan(concepts, lessons, mastery)
        recommendation = curriculum.recommend_next(plan)
        assert recommendation.kind == "review"
        assert recommendation.title == "Force"

    def test_an_unfinished_lesson_wins_over_everything(self):
        concepts = make_concepts()
        lessons = [Lesson(id=f"l{c.id}", concept_id=c.id, title=c.name) for c in concepts]
        plan = curriculum.build_plan(concepts, lessons, {})
        recommendation = curriculum.recommend_next(plan, in_progress_lesson_id="lb")
        assert recommendation.kind == "continue"
        assert recommendation.lesson_id == "lb"

    def test_no_plan_produces_no_recommendation(self):
        assert curriculum.recommend_next([]) is None

    def test_the_graph_only_contains_real_edges(self):
        concepts = make_concepts()
        concepts[0].prerequisites = ["ghost"]
        graph = curriculum.knowledge_graph(concepts, {})
        assert all(
            edge["from"] in {c.id for c in concepts} for edge in graph["edges"]
        )
