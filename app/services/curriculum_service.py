"""Turning uploaded material into a course.

    resources -> passages -> concepts -> prerequisites -> lessons -> questions

Runs as a background job so the interface stays responsive, reporting each step
in words a student can read.

Reanalysis is safe to repeat. Concepts, lessons and questions are replaced
wholesale, while mastery, sessions and bookmarks survive by design: a student
who reuploads a corrected chapter should not lose what they have already
learned.
"""

from __future__ import annotations

import logging

from ..domain.models import (
    Concept,
    Lesson,
    LessonSection,
    ProcessingStatus,
    Question,
    Resource,
    ResourceSection,
    SectionKind,
    SpaceStatus,
    new_id,
)
from .documents import pipeline
from .documents.base import UnreadableDocument, UnsupportedDocument
from .jobs import JobHandle, Step
from .providers.base import LessonDraft, TeachingEngine
from .retrieval import RetrievalService

logger = logging.getLogger("aloud.curriculum")

#: Below this many words, there is not enough to build a course from.
MINIMUM_WORDS = 120


class NotEnoughMaterial(ValueError):
    """Raised when a space cannot support a course yet."""


class CurriculumService:
    """Builds and rebuilds the course for a learning space."""

    def __init__(
        self, repos, engine: TeachingEngine, retrieval: RetrievalService, stt=None
    ) -> None:
        self.repos = repos
        self.engine = engine
        self.retrieval = retrieval
        # Only needed for recordings; documents never touch it.
        self.stt = stt

    # -- ingestion ---------------------------------------------------------

    def ingest_resource(self, resource: Resource) -> list[ResourceSection]:
        """Extract, clean and chunk one resource into passages."""
        repos = self.repos
        repos.resources.update_status(resource.id, ProcessingStatus.EXTRACTING)
        try:
            document = pipeline.extract(
                pipeline.Path(resource.stored_path), stt=self.stt
            )
        except (UnreadableDocument, UnsupportedDocument) as error:
            repos.resources.update_status(resource.id, ProcessingStatus.FAILED, error=str(error))
            raise

        repos.resources.update_status(resource.id, ProcessingStatus.STRUCTURING)
        sections = pipeline.to_sections(
            document, resource_id=resource.id, space_id=resource.space_id
        )
        if not sections:
            message = "No teachable text was found in this file."
            repos.resources.update_status(resource.id, ProcessingStatus.FAILED, error=message)
            raise UnreadableDocument(message)

        repos.sections.add_many(sections)
        repos.resources.update_status(
            resource.id,
            ProcessingStatus.READY,
            page_count=document.page_count,
            section_count=len(sections),
            word_count=document.word_count,
            title=resource.title or document.title,
        )
        self.retrieval.invalidate(resource.space_id)
        return sections

    # -- the full build ----------------------------------------------------

    def rebuild(self, space_id: str, user_id: str, handle: JobHandle | None = None) -> dict:
        """Analyse everything in a space and produce a fresh plan and lessons."""
        repos = self.repos
        space = repos.spaces.get(space_id, user_id)
        if space is None:
            raise NotEnoughMaterial("That subject could not be found.")

        def report(step: str, progress: float) -> None:
            if handle:
                handle.step(step, progress)

        repos.spaces.update(space_id, user_id, status=SpaceStatus.ANALYSING)

        # 1. Read anything not yet processed.
        report(Step.READING, 0.08)
        pending = [
            r for r in repos.resources.list_for_space(space_id)
            if r.status not in (ProcessingStatus.READY, ProcessingStatus.FAILED)
        ]
        for index, resource in enumerate(pending):
            try:
                self.ingest_resource(resource)
            except (UnreadableDocument, UnsupportedDocument) as error:
                # One bad file must not sink the whole subject.
                logger.warning("Skipping %s: %s", resource.id, error)
            report(Step.READING, 0.08 + 0.17 * ((index + 1) / max(1, len(pending))))

        sections = repos.sections.list_for_space(space_id)
        total_words = sum(s.word_count for s in sections)
        if total_words < MINIMUM_WORDS:
            repos.spaces.update(space_id, user_id, status=SpaceStatus.EMPTY)
            raise NotEnoughMaterial(
                "There is not enough readable text here yet to build a course. "
                "Add a document with more written content."
            )

        # 2. Understand it.
        report(Step.UNDERSTANDING, 0.3)
        self.retrieval.index_space(space_id, sections)
        model = self.engine.analyse(sections, subject_hint=space.title)
        if not model.concepts:
            repos.spaces.update(space_id, user_id, status=SpaceStatus.FAILED)
            raise NotEnoughMaterial(
                "No distinct topics could be found in this material. It may be a "
                "list or a set of figures rather than explanatory text."
            )

        for concept in model.concepts:
            concept.space_id = space_id

        # 3. Order it into a plan.
        report(Step.PLANNING, 0.45)
        repos.concepts.replace_for_space(space_id, model.concepts)
        repos.lessons.delete_for_space(space_id)
        repos.questions.delete_for_space(space_id)

        # 4. Write the lessons.
        report(Step.WRITING, 0.5)
        by_id = {s.id: s for s in sections}
        written = 0
        for index, concept in enumerate(model.concepts):
            sources = [by_id[sid] for sid in concept.section_ids if sid in by_id]
            if not sources:
                sources = self.retrieval.search(space_id, concept.name, limit=3, sections=sections)
            try:
                draft = self.engine.write_lesson(
                    concept, sources, prior_concepts=model.concepts
                )
            except Exception:  # noqa: BLE001 - one failed lesson must not lose the course
                logger.exception("Lesson generation failed for %s", concept.name)
                continue
            self._persist_lesson(space_id, concept, draft, ordinal=index)
            written += 1
            report(Step.WRITING, 0.5 + 0.45 * ((index + 1) / len(model.concepts)))

        if not written:
            repos.spaces.update(space_id, user_id, status=SpaceStatus.FAILED)
            raise NotEnoughMaterial("Lessons could not be written from this material.")

        if model.subject_summary and not space.description:
            repos.spaces.update(space_id, user_id, description=model.subject_summary[:400])
        repos.spaces.update(space_id, user_id, status=SpaceStatus.READY)

        result = {
            "concepts": len(model.concepts),
            "lessons": written,
            "sections": len(sections),
            "words": total_words,
        }
        if handle:
            handle.done(Step.DONE, **result)
        return result

    def _persist_lesson(
        self, space_id: str, concept: Concept, draft: LessonDraft, *, ordinal: int
    ) -> Lesson:
        """Write a drafted lesson, its sections and its questions."""
        lesson = Lesson(
            id=new_id("lsn"),
            space_id=space_id,
            concept_id=concept.id,
            title=draft.title or concept.name,
            subtitle=draft.subtitle,
            stage=concept.stage,
            ordinal=ordinal,
            estimated_minutes=draft.estimated_minutes,
        )

        sections: list[LessonSection] = []
        questions: list[Question] = []
        for index, drafted in enumerate(draft.sections):
            section = LessonSection(
                id=new_id("lsc"),
                lesson_id=lesson.id,
                ordinal=index,
                kind=drafted.kind,
                heading=drafted.heading,
                script=drafted.script,
                source_section_ids=drafted.source_section_ids,
                estimated_seconds=_speaking_seconds(drafted.script),
            )
            if drafted.question is not None:
                question = drafted.question
                question.id = question.id or new_id("qst")
                question.space_id = space_id
                question.concept_id = concept.id
                question.lesson_id = lesson.id
                questions.append(question)
                section.question_id = question.id
            elif drafted.kind in (SectionKind.CHECK, SectionKind.ASSESSMENT):
                # A check with no question is a narration beat, not a check.
                section.kind = SectionKind.EXPLANATION
            sections.append(section)

        self.repos.questions.add_many(questions)
        self.repos.lessons.create(lesson, sections)
        return lesson

    # -- reading the plan --------------------------------------------------

    def plan_for(self, space_id: str, user_id: str):
        """The study plan, recomputed from current understanding."""
        from ..domain.curriculum import build_plan

        concepts = self.repos.concepts.list_for_space(space_id)
        lessons = self.repos.lessons.list_for_space(space_id)
        mastery = self.repos.mastery.for_space(user_id, space_id)
        return build_plan(concepts, lessons, mastery)

    def sections_for_concept(self, concept: Concept, space_id: str) -> list[ResourceSection]:
        """The source passages behind a concept, for grounding."""
        found = self.repos.sections.by_ids(concept.section_ids)
        if found:
            return found
        return self.retrieval.search(
            space_id,
            concept.name,
            limit=3,
            sections=self.repos.sections.list_for_space(space_id),
        )

    def ensure_indexed(self, space_id: str) -> None:
        if not self.retrieval.is_indexed(space_id):
            self.retrieval.index_space(space_id, self.repos.sections.list_for_space(space_id))


def _speaking_seconds(script: str) -> int:
    """How long a passage takes to say, at an unhurried pace."""
    words = len(script.split())
    return max(3, round(words / 150 * 60))
