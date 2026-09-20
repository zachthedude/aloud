"""What kind of document is this?

Triage needs to know which uploads are past papers, the examiner refuses to
mark without a mark scheme, and the pattern hunter only looks at papers. So
every resource carries a role, guessed on upload and correctable by hand.

The guess is deliberately timid. Notes is the safe default: calling a set of
notes "notes" costs nothing, while calling something a mark scheme when it is
not would let the examiner mark against invented criteria.
"""

from __future__ import annotations

from app.domain.models import Resource, ResourceKind, ResourceRole
from app.services.documents.roles import guess_role


class TestGuessingFromTheFilename:
    def test_an_exam_board_mark_scheme_code(self):
        assert guess_role("9702_s22_ms_12.pdf") is ResourceRole.MARK_SCHEME

    def test_the_words_mark_scheme(self):
        assert guess_role("Physics Mark Scheme.pdf") is ResourceRole.MARK_SCHEME

    def test_a_mark_scheme_for_a_paper_is_a_mark_scheme(self):
        # Mark schemes name their paper, so the paper wording must not win.
        assert guess_role("Paper 1 Mark Scheme June 2023.pdf") is ResourceRole.MARK_SCHEME

    def test_an_exam_board_question_paper_code(self):
        assert guess_role("9702_s22_qp_12.pdf") is ResourceRole.PAST_PAPER

    def test_a_named_paper(self):
        assert guess_role("Paper 2 June 2023.pdf") is ResourceRole.PAST_PAPER

    def test_a_specification(self):
        assert guess_role("Physics Specification 2024.pdf") is ResourceRole.SYLLABUS

    def test_a_syllabus(self):
        assert guess_role("igcse-syllabus.pdf") is ResourceRole.SYLLABUS

    def test_anything_else_is_notes(self):
        assert guess_role("chapter-4-forces.docx") is ResourceRole.NOTES
        assert guess_role("IMG_2043.jpg") is ResourceRole.NOTES
        assert guess_role("") is ResourceRole.NOTES


class TestGuessingFromTheContents:
    def test_a_scan_named_nothing_useful(self):
        role = guess_role("scan001.pdf", "MARK SCHEME for the May/June 2022 series\n\n1 (a) ...")
        assert role is ResourceRole.MARK_SCHEME

    def test_a_paper_named_nothing_useful(self):
        role = guess_role("doc.pdf", "Cambridge International Examinations\nQuestion Paper\nPaper 1")
        assert role is ResourceRole.PAST_PAPER

    def test_short_codes_are_not_trusted_inside_prose(self):
        # "ms" means milliseconds far more often than it means mark scheme.
        assert guess_role("timings.docx", "the delay was 40 ms in every run") is ResourceRole.NOTES

    def test_only_the_opening_of_a_document_is_read(self):
        # A passing mention on page 40 does not make a document a mark scheme.
        buried = ("forces and motion. " * 400) + "mark scheme"
        assert guess_role("notes.pdf", buried) is ResourceRole.NOTES


class TestTheRoleSticks:
    def test_a_role_survives_the_database(self, repos):
        space = _a_space(repos)
        resource = Resource(
            space_id=space.id, title="Paper 1", kind=ResourceKind.PDF,
            role=ResourceRole.PAST_PAPER,
        )
        repos.resources.create(resource)
        assert repos.resources.get(resource.id).role is ResourceRole.PAST_PAPER

    def test_the_default_is_notes(self, repos):
        space = _a_space(repos)
        resource = Resource(space_id=space.id, title="Chapter 4", kind=ResourceKind.PDF)
        repos.resources.create(resource)
        assert repos.resources.get(resource.id).role is ResourceRole.NOTES

    def test_a_role_can_be_corrected(self, repos):
        space = _a_space(repos)
        resource = Resource(space_id=space.id, title="Scan", kind=ResourceKind.PDF)
        repos.resources.create(resource)
        repos.resources.set_role(resource.id, ResourceRole.MARK_SCHEME)
        assert repos.resources.get(resource.id).role is ResourceRole.MARK_SCHEME


def _a_space(repos):
    from app.domain.models import LearningSpace, User

    user = User(email="role@aloud.app", display_name="Role")
    repos.users.create(user)
    space = LearningSpace(user_id=user.id, title="Physics")
    return repos.spaces.create(space)
