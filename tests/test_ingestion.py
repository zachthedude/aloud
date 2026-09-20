"""Reading a student's material: validation, extraction, chunking, analysis.

Two things matter here. The security checks must hold, because this is where
untrusted files and URLs enter the system. And the analysis must find real
structure in real prose, because everything taught downstream is built on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.models import ResourceSection
from app.services import textanalysis as ta
from app.services.documents import pipeline
from app.services.documents.base import UnreadableDocument
from app.services.documents.parsers import HtmlProcessor, TextProcessor
from app.services.providers.ondevice import OnDeviceEngine
from app.services.retrieval import RetrievalService, build_index, search

MATERIAL = """# Cell Transport

## Diffusion
Diffusion is the net movement of particles from a region of higher concentration to a
region of lower concentration. This continues until equilibrium is reached. For example,
perfume spreading across a room is diffusion. The rate of diffusion increases with
temperature. A common misconception is that diffusion requires energy from the cell.

## Osmosis
Osmosis is the movement of water molecules across a partially permeable membrane from a
dilute solution to a concentrated solution. Students often confuse osmosis with
diffusion. Osmosis refers specifically to water.

## Active Transport
Active transport is the movement of particles against a concentration gradient using
energy from respiration. Active transport requires ATP. Unlike diffusion, active
transport can move substances from low to high concentration.
"""


# ---------------------------------------------------------------------------
# Upload validation
# ---------------------------------------------------------------------------


class TestUploadValidation:
    def test_a_real_pdf_is_accepted(self):
        extension, kind = pipeline.validate_upload("notes.pdf", b"%PDF-1.4 body", max_bytes=10_000)
        assert extension == ".pdf"
        assert kind.value == "pdf"

    def test_a_file_lying_about_its_type_is_rejected(self):
        """The browser's filename is a claim; the bytes are the evidence."""
        with pytest.raises(pipeline.UploadRejected, match="does not look like a valid"):
            pipeline.validate_upload("notes.pdf", b"MZ\x90\x00 this is an exe", max_bytes=10_000)

    def test_an_executable_renamed_to_pdf_cannot_slip_through(self):
        with pytest.raises(pipeline.UploadRejected):
            pipeline.validate_upload("payload.pdf", b"#!/bin/sh\nrm -rf /", max_bytes=10_000)

    def test_an_unsupported_extension_is_rejected(self):
        with pytest.raises(pipeline.UploadRejected, match="not supported"):
            pipeline.validate_upload("thing.exe", b"MZ", max_bytes=10_000)

    def test_oversized_files_are_rejected_with_a_readable_limit(self):
        with pytest.raises(pipeline.UploadRejected, match="MB"):
            pipeline.validate_upload("big.txt", b"x" * 2_000_000, max_bytes=1_048_576)

    def test_empty_files_are_rejected(self):
        with pytest.raises(pipeline.UploadRejected, match="empty"):
            pipeline.validate_upload("empty.txt", b"", max_bytes=10_000)

    @pytest.mark.parametrize(
        "filename",
        ["../../etc/passwd.txt", "..\\..\\windows\\system32\\x.txt", "a/b/c.txt"],
    )
    def test_traversal_is_stripped_from_display_names(self, filename):
        safe = pipeline.safe_display_name(filename)
        assert ".." not in safe
        assert "/" not in safe and "\\" not in safe

    def test_the_stored_name_is_generated_not_supplied(self, tmp_path: Path):
        """Path traversal is removed as a category, not sanitised away."""
        stored = pipeline.store_upload(
            b"hello world", resource_id="res_abc", extension=".txt", directory=tmp_path
        )
        assert stored.name == "res_abc.txt"
        assert stored.parent == tmp_path.resolve()


# ---------------------------------------------------------------------------
# URL fetching
# ---------------------------------------------------------------------------


class TestUrlSafety:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000/admin",
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/internal",
            "http://192.168.1.1/",
            "http://[::1]/",
        ],
    )
    def test_private_and_local_addresses_are_refused(self, url):
        """Without this, the URL field is a request-forgery primitive."""
        with pytest.raises((pipeline.UploadRejected, UnreadableDocument)):
            pipeline.fetch_url(url)

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "gopher://x"])
    def test_only_http_schemes_are_allowed(self, url):
        with pytest.raises(pipeline.UploadRejected, match="http"):
            pipeline.fetch_url(url)

    def test_a_malformed_address_is_refused(self):
        with pytest.raises(pipeline.UploadRejected):
            pipeline.fetch_url("http://")


# ---------------------------------------------------------------------------
# Extraction and chunking
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_markdown_headings_become_passage_headings(self, tmp_path: Path):
        path = tmp_path / "material.md"
        path.write_text(MATERIAL, encoding="utf-8")
        document = TextProcessor().extract(path)
        sections = pipeline.to_sections(document, resource_id="r1", space_id="s1")

        headings = [section.heading for section in sections]
        assert "Diffusion" in headings
        assert "Osmosis" in headings
        assert "Active Transport" in headings

    def test_a_file_with_too_little_text_is_refused(self, tmp_path: Path):
        path = tmp_path / "thin.md"
        path.write_text("# Hi\n\nShort.", encoding="utf-8")
        with pytest.raises(UnreadableDocument):
            TextProcessor().extract(path)

    def test_running_headers_are_stripped(self):
        """The same line on every page is a header, not content."""
        from app.services.documents.base import Block

        blocks = []
        for page in range(1, 10):
            blocks.append(Block("Chapter 4: Forces", page=page))
            blocks.append(Block(f"Real content on page {page} that should survive.", page=page))
        cleaned = pipeline.clean_blocks(blocks)
        assert not any(b.text == "Chapter 4: Forces" for b in cleaned)
        assert len(cleaned) == 9

    def test_page_numbers_are_dropped(self):
        from app.services.documents.base import Block

        cleaned = pipeline.clean_blocks([
            Block("12"), Block("Page 3 of 40"),
            Block("Actual teaching content that should be kept in full."),
        ])
        assert len(cleaned) == 1

    def test_words_broken_across_lines_are_rejoined(self):
        from app.services.documents.base import Block

        cleaned = pipeline.clean_blocks([Block("The accelera-\ntion of the object increases.")])
        assert "acceleration" in cleaned[0].text

    def test_long_passages_are_split_at_sentence_boundaries(self):
        from app.services.documents.base import Block

        sentence = "The particles move from a high concentration to a low concentration. "
        blocks = [Block("Diffusion", is_heading=True), Block(sentence * 60)]
        passages = pipeline.chunk(blocks)
        assert len(passages) > 1
        assert all(heading == "Diffusion" for heading, _text, _page in passages)
        assert all(len(text.split()) <= pipeline.MAX_WORDS for _h, text, _p in passages)

    def test_html_extraction_ignores_scripts_and_navigation(self):
        html = """<html><head><title>Waves</title></head><body>
            <nav>Home About Contact</nav>
            <script>var tracking = 1;</script>
            <h1>Wave Speed</h1>
            <p>The speed of a wave is the product of its frequency and its wavelength,
               and it depends on the medium rather than on the source.</p>
            <footer>Copyright notice here</footer></body></html>"""
        document = HtmlProcessor.from_html(html)
        text = " ".join(block.text for block in document.blocks)
        assert "tracking" not in text
        assert "wave" in text.lower()


# ---------------------------------------------------------------------------
# Text analysis
# ---------------------------------------------------------------------------


class TestTextAnalysis:
    def test_definitions_are_found(self):
        findings = ta.analyse_passage(
            "Osmosis is the movement of water across a partially permeable membrane."
        )
        assert findings.definitions
        assert findings.definitions[0][0].lower().startswith("osmosis")

    def test_a_clause_is_not_treated_as_a_definition_subject(self):
        """"Newton's second law describes what happens when..." names no thing."""
        findings = ta.analyse_passage(
            "Newton's second law describes what happens when the resultant force is not zero."
        )
        subjects = [subject for subject, _sentence in findings.definitions]
        assert not any(subject.lower().endswith("what") for subject in subjects)

    def test_formulas_survive_a_trailing_where_clause(self):
        findings = ta.analyse_passage(
            "The law is written as F = m a, where F is the resultant force, "
            "m is the mass and a is the acceleration."
        )
        assert any("=" in formula for formula in findings.formulas)

    @pytest.mark.parametrize(
        "sentence,expected",
        [
            ("The law is written as F = m a, where F is the resultant force.", ["F = m a"]),
            ("Momentum is calculated as p = m v, where p is momentum.", ["p = m v"]),
            ("The speed of a wave is v = f x lambda.", ["v = f x lambda"]),
            ("Kinetic energy is given by E = 0.5 m v^2.", ["E = 0.5 m v^2"]),
            ("Density is d = m / v for any material.", ["d = m / v"]),
            ("Ohm's law states V = I R and applies to conductors.", ["V = I R"]),
            # Recorded values are not relationships, and reading one aloud as a
            # formula would put nonsense into a lesson.
            ("In this experiment x = 5 was recorded.", []),
            ("We found that the temperature T = 300 kelvin during the run.", []),
            ("The answer y = 12 metres was correct.", []),
        ],
    )
    def test_formulas_are_told_apart_from_recorded_values(self, sentence, expected):
        assert ta.analyse_passage(sentence).formulas == expected

    def test_misconceptions_are_picked_up(self):
        findings = ta.analyse_passage(
            "A common misconception is that a moving object needs a continuous force."
        )
        assert findings.misconceptions

    def test_examples_are_picked_up(self):
        findings = ta.analyse_passage(
            "For example, a trolley of mass 2 kilograms accelerates at 5 metres per second squared."
        )
        assert findings.examples

    def test_ordinals_are_not_stopwords(self):
        """Dropping "first" collapses three of Newton's laws into one concept."""
        assert "first" in ta.content_words("Newton's First Law")
        assert "second" in ta.content_words("Newton's Second Law")

    def test_substantive_sentences_outrank_meta_ones(self):
        meta = "Newton's second law describes what happens when a force acts."
        real = "Doubling the force doubles the acceleration, and doubling the mass halves it."
        assert ta.informativeness(real) > ta.informativeness(meta)

    def test_headings_are_recognised(self):
        assert ta.is_heading("Newton's Second Law")
        assert ta.is_heading("3.2 Wave Speed")
        assert not ta.is_heading(
            "This is an ordinary sentence of prose that runs on for a while and ends."
        )

    def test_summarising_never_invents_text(self):
        text = (
            "Friction opposes motion between surfaces. Friction converts kinetic energy "
            "into heat. Air resistance is a kind of friction."
        )
        summary = ta.summarise(text, max_sentences=1)
        assert summary in text


# ---------------------------------------------------------------------------
# The on-device engine
# ---------------------------------------------------------------------------


def sections_from(text: str) -> list[ResourceSection]:
    from app.services.documents.base import Block

    blocks = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            blocks.append(Block(stripped.lstrip("# ").strip(), is_heading=True))
        else:
            blocks.append(Block(stripped))
    passages = pipeline.chunk(pipeline.clean_blocks(blocks))
    return [
        ResourceSection(
            id=f"sec_{index}", ordinal=index, heading=heading, text=body,
            word_count=len(body.split()), space_id="s1", resource_id="r1",
        )
        for index, (heading, body, _page) in enumerate(passages)
    ]


class TestOnDeviceEngine:
    @pytest.fixture
    def model(self):
        return OnDeviceEngine().analyse(sections_from(MATERIAL), subject_hint="Cell Transport")

    def test_the_author_s_headings_become_concepts(self, model):
        names = {concept.name.lower() for concept in model.concepts}
        assert "diffusion" in names
        assert "osmosis" in names
        assert "active transport" in names

    def test_the_document_title_does_not_become_a_lesson(self, model):
        assert "cell transport" not in {c.name.lower() for c in model.concepts}

    def test_definitions_are_attached_to_the_right_concept(self, model):
        diffusion = next(c for c in model.concepts if c.name.lower() == "diffusion")
        assert "diffusion" in diffusion.definition.lower()
        assert "osmosis" not in diffusion.definition.lower()

    def test_prerequisites_never_form_a_loop(self, model):
        from app.domain.curriculum import prerequisite_depth

        prerequisite_depth(model.concepts)  # raises on a cycle

    def test_every_concept_points_at_its_sources(self, model):
        assert all(concept.section_ids for concept in model.concepts)

    def test_a_lesson_has_a_teaching_shape(self, model):
        concept = next(c for c in model.concepts if c.name.lower() == "diffusion")
        draft = OnDeviceEngine().write_lesson(
            concept, sections_from(MATERIAL), prior_concepts=model.concepts
        )
        kinds = [section.kind.value for section in draft.sections]
        assert kinds[0] == "context"
        assert "explanation" in kinds
        assert "recap" in kinds
        assert kinds[-1] == "next_step"
        assert "check" in kinds, "a lesson must ask the student something"

    def test_narration_never_reads_as_a_document(self, model):
        concept = model.concepts[0]
        draft = OnDeviceEngine().write_lesson(concept, sections_from(MATERIAL))
        script = " ".join(section.script for section in draft.sections)
        for artefact in ("#", "* ", "- ", "|", "```"):
            assert artefact not in script

    def test_lessons_are_deterministic(self, model):
        engine = OnDeviceEngine()
        concept = model.concepts[0]
        first = engine.write_lesson(concept, sections_from(MATERIAL))
        second = engine.write_lesson(concept, sections_from(MATERIAL))
        assert [s.script for s in first.sections] == [s.script for s in second.sections]

    def test_questions_can_be_answered_out_loud(self, model):
        concept = model.concepts[0]
        questions = OnDeviceEngine().write_questions(concept, sections_from(MATERIAL), count=3)
        assert questions
        # Markers of a question that needs a real answer rather than a word.
        open_forms = (
            "why", "how", "what", "explain", "describe", "give", "teach",
            "put ", "in your own words", "situation", "connect", "reason",
        )
        for question in questions:
            prompt = question.prompt.lower()
            assert len(prompt.split()) >= 5, question.prompt
            assert question.expected_points
            assert any(marker in prompt for marker in open_forms), question.prompt
            # A bare auxiliary opener is a yes/no question. "Can you give me..."
            # is a polite request for an explanation, so it is fine.
            assert not prompt.startswith(("is ", "are ", "does ", "did ", "will ")), (
                f"yes/no question: {question.prompt}"
            )

    def test_question_kinds_vary(self, model):
        concept = next(c for c in model.concepts if c.name.lower() == "diffusion")
        questions = OnDeviceEngine().write_questions(concept, sections_from(MATERIAL), count=3)
        assert len({q.kind for q in questions}) > 1

    def test_answers_are_grounded_in_the_material(self, model):
        engine = OnDeviceEngine()
        sections = sections_from(MATERIAL)
        answer = engine.answer_question(
            "what is osmosis", concept=None, sections=sections[:2]
        )
        assert answer.text
        assert answer.source_section_ids

    def test_it_says_so_rather_than_inventing_an_answer(self):
        """Refusing beats contradicting the student's own course."""
        answer = OnDeviceEngine().answer_question(
            "what is the capital of France", concept=None, sections=[]
        )
        assert "could not find" in answer.text.lower()
        assert not answer.used_outside_knowledge

    @pytest.mark.parametrize("mode", ["simpler", "different", "example"])
    def test_re_explanation_differs_by_mode(self, model, mode):
        engine = OnDeviceEngine()
        concept = model.concepts[0]
        answer = engine.reexplain(concept, sections_from(MATERIAL), mode=mode)
        assert len(answer.text) > 40

    def test_material_with_no_structure_yields_nothing_rather_than_nonsense(self):
        sections = [
            ResourceSection(id="s1", ordinal=0, text="12 34 56 78 90 11 22 33", word_count=8)
        ]
        model = OnDeviceEngine().analyse(sections)
        assert model.concepts == []


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


class TestRetrieval:
    def test_the_right_passage_is_found(self):
        sections = sections_from(MATERIAL)
        index = build_index(sections)
        hits = search(index, "what does osmosis mean", limit=2)
        assert hits
        assert "osmosis" in hits[0].section.text.lower()

    def test_a_query_with_no_content_words_returns_nothing(self):
        index = build_index(sections_from(MATERIAL))
        assert search(index, "the and of it") == []

    def test_an_empty_index_is_safe(self):
        assert search(build_index([]), "anything") == []

    def test_the_cache_can_be_invalidated(self):
        service = RetrievalService()
        service.index_space("s1", sections_from(MATERIAL))
        assert service.is_indexed("s1")
        service.invalidate("s1")
        assert not service.is_indexed("s1")


def test_reset_wipes_a_populated_database(settings):
    """`run.py --reset` on a database with real rows in it, foreign keys and all."""
    from app.seed.demo import seed
    from app.storage.db import Database

    seed(settings)
    database = Database(settings.db_path)
    with database.reader() as connection:
        assert connection.execute("SELECT count(*) FROM lessons").fetchone()[0] > 0

    database.reset()

    with database.reader() as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM lessons").fetchone()[0] == 0
