"""The first two modes, and the rules every mode must keep.

Quarantine asks, marks and records. Last 90 seconds only speaks. They are the
two opposite shapes a mode can take, which is why they were built first: if
both fit the same contract without either owning any audio or storage, the
contract holds for the other fourteen.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app import features
from app.domain import learner
from app.domain.assessment import AnswerAnalysis
from app.domain.learner import Observation
from app.domain.models import Concept, Judgement, MasteryRecord, Question, utcnow
from app.domain.turn import MAX_SENTENCES, Reply, Turn, sentence_count
from app.services.modes import registry
from app.services.modes.base import ModeContext
from app.services.modes.last90 import Last90
from app.services.modes.quarantine import Quarantine

NOW = utcnow()


# ---------------------------------------------------------------------------
# Fixtures: a small subject and a learner who knows some of it
# ---------------------------------------------------------------------------


def concept(key, name, *, definition="", formulas=(), traps=(), weight=0.2):
    return Concept(
        id=f"cpt_{key}", name=name, definition=definition, formulas=list(formulas),
        misconceptions=list(traps), section_ids=[f"sec_{key}"], exam_weight=weight,
    )


FORCE = concept("force", "Force", definition="A force is a push or a pull that acts on an object.",
                formulas=["F = m a"], traps=["Students often confuse mass and weight."], weight=0.4)
FRICTION = concept("friction", "Friction",
                   definition="Friction is a force that opposes motion between surfaces in contact.",
                   traps=["A moving object does not need a continuous force to keep moving."], weight=0.3)
MOMENTUM = concept("momentum", "Momentum", definition="Momentum is mass times velocity.",
                   formulas=["p = m v"], weight=0.3)

QUESTIONS = {
    c.id: [
        Question(id=f"qst_{c.id}_{n}", concept_id=c.id, prompt=f"In your own words, what is {c.name.lower()}?",
                 expected_points=[f"{c.name} point {n}"])
        for n in range(2)
    ]
    for c in (FORCE, FRICTION, MOMENTUM)
}


def record(c, **fields):
    return MasteryRecord(user_id="usr", space_id="spc", concept_id=c.id, **fields)


def known(c):
    """A concept answered fluently and correctly several times."""
    state = record(c)
    for _ in range(4):
        state = learner.record(state, Observation("correct", latency_ms=1200), now=NOW)
    return state


def quarantined(c, strikes=3):
    state = record(c)
    for _ in range(strikes):
        state = learner.record(state, Observation("wrong"), now=NOW)
    assert state.quarantined
    return state


class Harness:
    """A context with a real learner model and a scripted evaluator."""

    def __init__(self, records, *, speed=1.0, judgement=Judgement.CORRECT):
        self.records = dict(records)
        self.judgement = judgement
        self.cursor: dict = {}
        self.speed = speed
        self.observed: list[str] = []

    def context(self) -> ModeContext:
        concepts = [(c, self.records.get(c.id, record(c))) for c in (FORCE, FRICTION, MOMENTUM)]
        return ModeContext(
            user_id="usr", space_id="spc", cursor=self.cursor, concepts=concepts,
            questions_for=lambda cid: QUESTIONS.get(cid, []),
            evaluate=self._evaluate, observe=self._observe, now=NOW, speed=self.speed,
        )

    def _evaluate(self, text, question, _concept):
        missed = [] if self.judgement is Judgement.CORRECT else list(question.expected_points)
        return AnswerAnalysis(judgement=self.judgement, confidence=0.9, missed_points=missed)

    def _observe(self, concept_id, analysis, reply):
        self.observed.append(concept_id)
        before = self.records.get(concept_id) or record(next(
            c for c in (FORCE, FRICTION, MOMENTUM) if c.id == concept_id))
        after = learner.record(
            before, Observation(learner.outcome_for(analysis.judgement), reply.latency_ms, reply.text),
            now=NOW,
        )
        self.records[concept_id] = after
        return after

    def run(self, mode, replies=40):
        """Open the mode and keep answering or advancing until it is done."""
        turns = [mode.open(self.context())]
        while not turns[-1].done and len(turns) < replies:
            action = "" if turns[-1].expects == "answer" else "next"
            turns.append(mode.respond(self.context(), Reply(text="an answer", action=action)))
        return turns


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class TestTheContract:
    def test_every_implemented_mode_is_on_by_default(self):
        for key in registry.MODES:
            assert features.DEFAULTS[key] is True, f"{key} is built but defaults off"

    def test_every_unimplemented_mode_is_off_by_default(self):
        for key, on in features.DEFAULTS.items():
            if key not in registry.MODES:
                assert on is False, f"{key} defaults on but is not implemented"

    def test_a_flag_cannot_switch_on_a_mode_that_does_not_exist(self):
        flags = features.resolve("all")
        effective = registry.effective_flags(flags)
        assert effective["triage"] is (("triage" in registry.MODES))
        assert effective["quarantine"] is True

    def test_registered_keys_are_all_in_the_catalogue(self):
        assert set(registry.MODES) <= set(features.DEFAULTS)

    @pytest.mark.parametrize("mode_key", sorted(registry.MODES))
    def test_no_turn_is_longer_than_three_sentences(self, mode_key):
        mode = registry.MODES[mode_key]
        harness = Harness({
            FORCE.id: known(FORCE) if mode_key == "last90" else quarantined(FORCE),
            FRICTION.id: known(FRICTION) if mode_key == "last90" else quarantined(FRICTION),
        })
        for turn in harness.run(mode):
            assert sentence_count(turn.say) <= MAX_SENTENCES, turn.say

    @pytest.mark.parametrize("mode_key", sorted(registry.MODES))
    def test_every_teaching_turn_is_grounded(self, mode_key):
        """A turn that names a concept must say which material it came from."""
        mode = registry.MODES[mode_key]
        harness = Harness({FORCE.id: known(FORCE) if mode_key == "last90" else quarantined(FORCE)})
        for turn in harness.run(mode):
            if turn.concept_id:
                assert turn.grounded_in, turn.say

    def test_turns_reject_an_unknown_expectation(self):
        with pytest.raises(ValueError):
            Turn(say="hello", expects="dance")

    def test_sentence_counting_ignores_decimals(self):
        assert sentence_count("g is 9.8 metres per second squared.") == 1
        assert sentence_count("One. Two! Three?") == 3


# ---------------------------------------------------------------------------
# Last 90 seconds: only what is already known, nothing new
# ---------------------------------------------------------------------------


class TestLast90:
    def test_nothing_known_says_so_and_stops(self):
        turns = Harness({}).run(Last90())
        assert len(turns) == 1 and turns[0].done
        assert "already know" in turns[0].say.lower()

    def test_only_known_concepts_are_recapped(self):
        harness = Harness({FORCE.id: known(FORCE), FRICTION.id: record(FRICTION)})
        spoken = " ".join(turn.say for turn in harness.run(Last90()))
        assert "push or a pull" in spoken
        assert "Friction" not in spoken, "an unknown concept must never appear"

    def test_it_never_asks_anything(self):
        harness = Harness({FORCE.id: known(FORCE), MOMENTUM.id: known(MOMENTUM)})
        assert all(turn.expects == "nothing" for turn in harness.run(Last90()))

    def test_it_says_the_formula_and_the_trap(self):
        spoken = " ".join(t.say for t in Harness({FORCE.id: known(FORCE)}).run(Last90()))
        assert "F = m a" in spoken
        assert "confuse mass and weight" in spoken

    def test_heaviest_topic_first(self):
        harness = Harness({FORCE.id: known(FORCE), MOMENTUM.id: known(MOMENTUM)})
        turns = [t for t in harness.run(Last90()) if t.concept_id]
        assert turns[0].concept_id == FORCE.id

    def test_it_fits_in_ninety_seconds(self):
        everything = {c.id: known(c) for c in (FORCE, FRICTION, MOMENTUM)}
        words = sum(len(t.say.split()) for t in Harness(everything).run(Last90()))
        assert words / learner_words_per_second(1.0) <= 90

    def test_a_faster_voice_fits_more_in(self):
        everything = {c.id: known(c) for c in (FORCE, FRICTION, MOMENTUM)}
        slow = [t for t in Harness(everything, speed=0.5).run(Last90()) if t.concept_id]
        fast = [t for t in Harness(everything, speed=2.0).run(Last90()) if t.concept_id]
        assert len(fast) >= len(slow)

    def test_something_forgotten_since_is_left_out(self):
        faded = known(FORCE)
        faded.last_seen_at = NOW - timedelta(days=30)
        spoken = " ".join(t.say for t in Harness({FORCE.id: faded}).run(Last90()))
        assert "push or a pull" not in spoken

    def test_it_writes_nothing_to_the_learner_model(self):
        harness = Harness({FORCE.id: known(FORCE)})
        harness.run(Last90())
        assert harness.observed == []


def learner_words_per_second(speed):
    return 150 / 60 * speed


# ---------------------------------------------------------------------------
# Quarantine: three misses in, two clean answers in a row out
# ---------------------------------------------------------------------------


class TestQuarantine:
    def test_an_empty_quarantine_says_how_things_get_in(self):
        turns = Harness({FORCE.id: known(FORCE)}).run(Quarantine())
        assert len(turns) == 1 and turns[0].done
        assert "three" in turns[0].say.lower()

    def test_it_asks_about_a_quarantined_concept(self):
        harness = Harness({FRICTION.id: quarantined(FRICTION), FORCE.id: known(FORCE)})
        first = Quarantine().open(harness.context())
        assert first.expects == "answer"
        assert first.concept_id == FRICTION.id
        assert first.question_id.startswith(f"qst_{FRICTION.id}")

    def test_the_worst_goes_first(self):
        harness = Harness({FRICTION.id: quarantined(FRICTION), FORCE.id: quarantined(FORCE, strikes=5)})
        assert Quarantine().open(harness.context()).concept_id == FORCE.id

    def test_every_answer_is_recorded_in_the_learner_model(self):
        harness = Harness({FRICTION.id: quarantined(FRICTION)})
        mode = Quarantine()
        mode.open(harness.context())
        mode.respond(harness.context(), Reply(text="friction opposes motion", latency_ms=1800))
        assert harness.observed == [FRICTION.id]
        assert harness.records[FRICTION.id].latencies[-1] == 1800

    def test_two_clean_answers_let_it_out(self):
        harness = Harness({FRICTION.id: quarantined(FRICTION)})
        turns = harness.run(Quarantine())
        assert not harness.records[FRICTION.id].quarantined
        assert any("out of quarantine" in t.say.lower() for t in turns)
        assert turns[-1].done

    def test_a_wrong_answer_names_what_was_missing(self):
        harness = Harness({FRICTION.id: quarantined(FRICTION)}, judgement=Judgement.INCORRECT)
        mode = Quarantine()
        mode.open(harness.context())
        feedback = mode.respond(harness.context(), Reply(text="no idea"))
        assert "Friction point" in feedback.say
        assert feedback.grounded_in

    def test_feedback_never_scolds(self):
        harness = Harness({FRICTION.id: quarantined(FRICTION)}, judgement=Judgement.INCORRECT)
        for turn in harness.run(Quarantine()):
            for word in ("wrong", "failed", "bad", "should have"):
                assert word not in turn.say.lower(), turn.say

    def test_a_sitting_has_an_end_even_if_nothing_clears(self):
        harness = Harness({FRICTION.id: quarantined(FRICTION)}, judgement=Judgement.INCORRECT)
        turns = harness.run(Quarantine(), replies=200)
        assert turns[-1].done, "a drill that never ends is not a drill"
        assert len(turns) < 40

    def test_skipping_moves_on_without_recording(self):
        harness = Harness({FRICTION.id: quarantined(FRICTION), FORCE.id: quarantined(FORCE, strikes=4)})
        mode = Quarantine()
        first = mode.open(harness.context())
        after = mode.respond(harness.context(), Reply(action="skip"))
        assert harness.observed == []
        assert after.concept_id != first.concept_id

    def test_the_same_question_is_not_asked_twice_in_a_row(self):
        harness = Harness({FRICTION.id: quarantined(FRICTION)})
        asked = [t.question_id for t in harness.run(Quarantine()) if t.expects == "answer"]
        assert all(a != b for a, b in zip(asked, asked[1:]))
