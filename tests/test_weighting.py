"""What each topic is worth.

Triage ranks topics by marks times weakness, so it needs a number per topic.
That number comes from evidence the student uploaded, and the basis for it is
carried alongside so it can be said out loud: "worth about a fifth of the
paper, going by the last four papers" is a claim Aloud can defend.

With no past papers and no syllabus there is no evidence, so every topic
weighs the same and the basis says so. Inventing a weighting would make triage
confidently skip the wrong thing.
"""

from __future__ import annotations

from app.domain.models import Concept, ResourceRole
from app.services.weighting import Evidence, weigh


def concept(name, terms=()):
    return Concept(id=f"cpt_{name.lower().replace(' ', '_')}", name=name, terminology=list(terms))


CONCEPTS = [
    concept("Newton's second law", ["resultant force", "acceleration"]),
    concept("Momentum", ["momentum", "impulse"]),
    concept("Refraction", ["refraction", "snell"]),
]


class TestWithNoEvidence:
    def test_everything_weighs_the_same(self):
        weights = weigh(CONCEPTS, [])
        shares = {w.share for w in weights.values()}
        assert len(shares) == 1

    def test_the_shares_add_up_to_one(self):
        weights = weigh(CONCEPTS, [])
        assert abs(sum(w.share for w in weights.values()) - 1.0) < 1e-6

    def test_the_basis_admits_there_is_no_evidence(self):
        weights = weigh(CONCEPTS, [])
        basis = weights[CONCEPTS[0].id].basis
        assert "no past papers" in basis.lower()

    def test_nothing_is_claimed_about_marks(self):
        assert weigh(CONCEPTS, [])[CONCEPTS[0].id].papers_seen == 0


class TestWithPastPapers:
    def test_a_topic_asked_more_often_weighs_more(self):
        papers = [
            Evidence(ResourceRole.PAST_PAPER, "Calculate the resultant force on the trolley."),
            Evidence(ResourceRole.PAST_PAPER, "State Newton's second law and find the acceleration."),
            Evidence(ResourceRole.PAST_PAPER, "A ball has momentum of 4 kg m/s."),
        ]
        weights = weigh(CONCEPTS, papers)
        assert weights[CONCEPTS[0].id].share > weights[CONCEPTS[1].id].share

    def test_the_basis_counts_the_papers(self):
        papers = [
            Evidence(ResourceRole.PAST_PAPER, "resultant force"),
            Evidence(ResourceRole.PAST_PAPER, "resultant force again"),
        ]
        weights = weigh(CONCEPTS, papers)
        assert weights[CONCEPTS[0].id].papers_seen == 2
        assert "2 of 2" in weights[CONCEPTS[0].id].basis

    def test_a_topic_never_asked_still_gets_a_floor(self):
        # Absent from every paper is weak evidence, not proof it cannot come up.
        papers = [Evidence(ResourceRole.PAST_PAPER, "refraction and snell's law")]
        weights = weigh(CONCEPTS, papers)
        assert weights[CONCEPTS[1].id].share > 0

    def test_notes_are_not_counted_as_papers(self):
        notes = [Evidence(ResourceRole.NOTES, "resultant force " * 50)]
        weights = weigh(CONCEPTS, notes)
        assert weights[CONCEPTS[0].id].papers_seen == 0


class TestWithASyllabus:
    def test_a_topic_in_the_syllabus_outweighs_one_that_is_not(self):
        syllabus = [Evidence(ResourceRole.SYLLABUS, "Candidates should understand momentum and impulse.")]
        weights = weigh(CONCEPTS, syllabus)
        assert weights[CONCEPTS[1].id].share > weights[CONCEPTS[2].id].share

    def test_the_basis_says_the_syllabus_was_used(self):
        syllabus = [Evidence(ResourceRole.SYLLABUS, "momentum and impulse")]
        assert "syllabus" in weigh(CONCEPTS, syllabus)[CONCEPTS[1].id].basis.lower()

    def test_papers_and_syllabus_together_are_both_named(self):
        evidence = [
            Evidence(ResourceRole.SYLLABUS, "momentum and impulse"),
            Evidence(ResourceRole.PAST_PAPER, "calculate the momentum of the trolley"),
        ]
        basis = weigh(CONCEPTS, evidence)[CONCEPTS[1].id].basis.lower()
        assert "syllabus" in basis and "paper" in basis


class TestTheNumbersStaySane:
    def test_shares_always_add_up_to_one(self):
        evidence = [
            Evidence(ResourceRole.PAST_PAPER, "resultant force acceleration"),
            Evidence(ResourceRole.SYLLABUS, "momentum"),
        ]
        weights = weigh(CONCEPTS, evidence)
        assert abs(sum(w.share for w in weights.values()) - 1.0) < 1e-6

    def test_one_concept_takes_everything(self):
        only = [CONCEPTS[0]]
        assert weigh(only, [])[only[0].id].share == 1.0

    def test_no_concepts_is_not_a_crash(self):
        assert weigh([], []) == {}
