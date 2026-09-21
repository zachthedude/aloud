"""The learner model.

One row per student per concept, read by every mode. These tests pin the maths
that fourteen features will lean on: what counts as hesitation, when a right
answer is still shaky, how confidence fades, and when a concept is pulled into
quarantine and let back out.
"""

from __future__ import annotations

from datetime import timedelta

from app.domain import learner
from app.domain.learner import Observation
from app.domain.models import Judgement, MasteryRecord, utcnow

NOW = utcnow()


def fresh() -> MasteryRecord:
    return MasteryRecord(user_id="usr", space_id="spc", concept_id="cpt")


def answer(record, outcome, *, latency=None, transcript="", at=NOW):
    return learner.record(record, Observation(outcome, latency_ms=latency, transcript=transcript), now=at)


class TestReadingATranscript:
    def test_fillers_are_counted(self):
        assert learner.count_fillers("um so it's uh the force erm times the mass") == 3

    def test_hedges_count_as_fillers(self):
        assert learner.count_fillers("I think it's the resultant force, I guess") == 2

    def test_like_is_not_a_filler(self):
        # "acts like a spring" is physics, not hesitation.
        assert learner.count_fillers("the rope acts like a spring") == 0

    def test_filler_words_inside_other_words_do_not_count(self):
        assert learner.count_fillers("the hummingbird hums over the umbrella") == 0

    def test_self_corrections_are_counted(self):
        text = "it's the mass, no wait, the weight. Sorry, I mean the resultant force"
        assert learner.count_corrections(text) == 3

    def test_a_plain_answer_has_neither(self):
        text = "Force equals mass times acceleration."
        assert learner.count_fillers(text) == 0
        assert learner.count_corrections(text) == 0


class TestRecordingAnAnswer:
    def test_a_correct_answer_is_counted(self):
        record = answer(fresh(), "correct")
        assert (record.correct, record.wrong) == (1, 0)
        assert record.last_seen_at == NOW

    def test_latency_keeps_only_the_recent_window(self):
        record = fresh()
        for ms in range(1000, 13000, 1000):
            record = answer(record, "correct", latency=ms)
        assert len(record.latencies) == learner.LATENCY_WINDOW
        assert record.latencies[-1] == 12000

    def test_a_typed_answer_carries_no_latency(self):
        assert answer(fresh(), "correct", latency=None).latencies == []

    def test_fillers_and_corrections_accumulate(self):
        record = answer(fresh(), "correct", transcript="um it's the mass, no wait, the weight")
        assert record.fillers == 1
        assert record.corrections == 1

    def test_recording_does_not_mutate_the_original(self):
        original = fresh()
        answer(original, "correct")
        assert original.correct == 0

    def test_judgements_map_to_outcomes(self):
        assert learner.outcome_for(Judgement.CORRECT) == "correct"
        assert learner.outcome_for(Judgement.MOSTLY_CORRECT) == "correct"
        assert learner.outcome_for(Judgement.INCOMPLETE) == "partial"
        assert learner.outcome_for(Judgement.MISCONCEPTION) == "wrong"
        assert learner.outcome_for(Judgement.INCORRECT) == "wrong"
        assert learner.outcome_for(Judgement.UNCLEAR) == "missed"


class TestHesitation:
    def test_nothing_known_is_not_hesitant(self):
        assert learner.hesitation(fresh()) == 0.0

    def test_quick_clean_answers_are_fluent(self):
        record = fresh()
        for _ in range(3):
            record = answer(record, "correct", latency=1500, transcript="force is mass times acceleration")
        assert learner.hesitation(record) < 0.1

    def test_slow_filler_heavy_answers_are_hesitant(self):
        record = fresh()
        for _ in range(3):
            record = answer(record, "correct", latency=9000,
                            transcript="um er I think it's, no wait, uh the force")
        assert learner.hesitation(record) > 0.7

    def test_hesitation_stays_in_range(self):
        record = fresh()
        for _ in range(8):
            record = answer(record, "correct", latency=60000, transcript="um uh er erm hmm " * 10)
        assert 0.0 <= learner.hesitation(record) <= 1.0


class TestShaky:
    """Feature 4's whole point: right, but not sure."""

    def test_right_but_slow_is_shaky(self):
        record = fresh()
        for _ in range(3):
            record = answer(record, "correct", latency=9000, transcript="um I think, uh, the force")
        assert learner.is_shaky(record)

    def test_right_and_fluent_is_not_shaky(self):
        record = fresh()
        for _ in range(3):
            record = answer(record, "correct", latency=1200, transcript="the resultant force")
        assert not learner.is_shaky(record)

    def test_wrong_is_weak_not_shaky(self):
        record = fresh()
        for _ in range(3):
            record = answer(record, "wrong", latency=9000, transcript="um uh the mass")
        assert not learner.is_shaky(record)

    def test_one_answer_is_not_enough_to_call_it(self):
        record = answer(fresh(), "correct", latency=9000, transcript="um uh er")
        assert not learner.is_shaky(record)


class TestConfidence:
    def test_nothing_known_is_zero(self):
        assert learner.confidence(fresh(), now=NOW) == 0.0

    def test_more_right_answers_mean_more_confidence(self):
        one = answer(fresh(), "correct", latency=1500)
        three = one
        for _ in range(2):
            three = answer(three, "correct", latency=1500)
        assert learner.confidence(three, now=NOW) > learner.confidence(one, now=NOW)

    def test_one_right_answer_is_not_certainty(self):
        assert learner.confidence(answer(fresh(), "correct", latency=1500), now=NOW) < 0.75

    def test_hesitation_costs_confidence_even_when_right(self):
        fluent, halting = fresh(), fresh()
        for _ in range(3):
            fluent = answer(fluent, "correct", latency=1500, transcript="the resultant force")
            halting = answer(halting, "correct", latency=9000, transcript="um uh I think the force")
        assert learner.confidence(halting, now=NOW) < learner.confidence(fluent, now=NOW)

    def test_confidence_fades_with_time(self):
        record = fresh()
        for _ in range(4):
            record = answer(record, "correct", latency=1500)
        today = learner.confidence(record, now=NOW)
        later = learner.confidence(record, now=NOW + timedelta(days=4))
        assert later < today

    def test_wrong_answers_pull_it_down(self):
        record = fresh()
        for outcome in ("correct", "wrong", "wrong", "wrong"):
            record = answer(record, outcome, latency=1500)
        assert learner.confidence(record, now=NOW) < 0.4

    def test_confidence_stays_in_range(self):
        record = fresh()
        for _ in range(30):
            record = answer(record, "correct", latency=500)
        assert 0.0 <= learner.confidence(record, now=NOW) <= 1.0


class TestQuarantine:
    """Feature 15: three misses in, two clean clears in a row out."""

    def test_three_misses_quarantine_a_concept(self):
        record = fresh()
        for outcome in ("wrong", "missed", "wrong"):
            record = answer(record, outcome)
        assert record.quarantined

    def test_two_misses_do_not(self):
        record = fresh()
        for outcome in ("wrong", "wrong"):
            record = answer(record, outcome)
        assert not record.quarantined

    def test_partial_answers_are_not_strikes(self):
        record = fresh()
        for _ in range(5):
            record = answer(record, "partial")
        assert not record.quarantined

    def test_two_clears_in_a_row_release_it(self):
        record = fresh()
        for outcome in ("wrong", "wrong", "wrong", "correct", "correct"):
            record = answer(record, outcome)
        assert not record.quarantined
        assert record.strikes == 0

    def test_a_miss_between_clears_resets_the_run(self):
        record = fresh()
        for outcome in ("wrong", "wrong", "wrong", "correct", "wrong", "correct"):
            record = answer(record, outcome)
        assert record.quarantined

    def test_a_partial_between_clears_resets_the_run(self):
        record = fresh()
        for outcome in ("wrong", "wrong", "wrong", "correct", "partial", "correct"):
            record = answer(record, outcome)
        assert record.quarantined

    def test_a_released_concept_can_go_back_in(self):
        record = fresh()
        for outcome in ("wrong",) * 3 + ("correct",) * 2 + ("wrong",) * 3:
            record = answer(record, outcome)
        assert record.quarantined


class TestCatchingLies:
    """Feature 10 keeps its score here, next to everything else."""

    def test_a_catch_is_counted(self):
        record = learner.record_lie(fresh(), caught=True, now=NOW)
        record = learner.record_lie(record, caught=False, now=NOW)
        assert (record.catches, record.lies_heard) == (1, 2)
        assert learner.catch_rate(record) == 0.5

    def test_no_lies_heard_has_no_rate(self):
        assert learner.catch_rate(fresh()) is None
