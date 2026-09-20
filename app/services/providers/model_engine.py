"""A `TeachingEngine` backed by a hosted model.

Same interface as the on-device engine, so nothing above this layer knows which
one is teaching. Three properties matter here.

Grounded. Every prompt carries the student's own passages and instructs the
model to teach from them, flagging anything it adds from outside.

Degrading, not failing. A model call that errors, times out or returns
unparseable JSON falls through to the on-device engine. A student mid-lesson
gets a slightly plainer explanation, never an error screen.

Checked. Model judgements about answers still pass through
`assessment.sanity_check`, so a confident verdict on a two-word answer cannot
award mastery nobody earned.
"""

from __future__ import annotations

import json
import re

from ...domain.assessment import AnswerAnalysis, sanity_check
from ...domain.models import (
    Concept,
    Judgement,
    PlanStage,
    Question,
    QuestionKind,
    ResourceSection,
    SectionKind,
)
from .base import (
    DraftSection,
    GroundedAnswer,
    KnowledgeModel,
    LessonDraft,
    LLMProvider,
    ProviderUnavailable,
)
from .ondevice import OnDeviceEngine

TEACHER_SYSTEM = """You are a patient, academically rigorous private tutor teaching one student out loud.

The student is listening, not reading. Write what you would SAY.
- Speak in complete, flowing sentences. No bullet points, no headings, no markdown, no symbols read aloud badly.
- Explain, do not recite. Give analogies and concrete examples.
- Never rush the student. Never mention time limits, scores, streaks or grades.
- Do not open with praise. No "Great question!", no "Amazing!". Encouragement only when it is earned and specific.
- The student's own uploaded material is the authority. Teach from it.
- If you must use knowledge beyond their material, say so plainly in the sentence itself.

Return only what is asked for, in the exact format requested."""

_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)

#: How much source text to hand the model per call.
MAX_CONTEXT_CHARS = 12000


def _extract_json(text: str) -> object:
    """Pull JSON out of a response that may be wrapped in prose or fences."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(stripped)
    if not match:
        raise ValueError("No JSON found in model response")
    return json.loads(match.group(0))


def _context(sections: list[ResourceSection], limit: int = MAX_CONTEXT_CHARS) -> str:
    parts: list[str] = []
    used = 0
    for section in sections:
        heading = section.heading or f"Passage {section.ordinal + 1}"
        block = f"[{section.id}] {heading}\n{section.text}\n"
        if used + len(block) > limit:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


class ModelBackedEngine:
    """Teaching operations delegated to an `LLMProvider`."""

    name = "model"
    grounded_only = False

    def __init__(self, llm: LLMProvider, fallback: OnDeviceEngine | None = None) -> None:
        self._llm = llm
        self._fallback = fallback or OnDeviceEngine()

    @property
    def display_name(self) -> str:
        return f"Claude ({getattr(self._llm, 'model', self._llm.name)})"

    def _ask(self, prompt: str, *, max_tokens: int = 2000, temperature: float = 0.4) -> object:
        raw = self._llm.complete(
            TEACHER_SYSTEM, prompt, max_tokens=max_tokens, temperature=temperature
        )
        return _extract_json(raw)

    # -- analysis ----------------------------------------------------------

    def analyse(
        self, sections: list[ResourceSection], *, subject_hint: str = ""
    ) -> KnowledgeModel:
        if not sections:
            return KnowledgeModel(suggested_title=subject_hint)

        # Start from the on-device reading so section grounding and ordering are
        # already sound, then let the model improve names, summaries and links.
        base = self._fallback.analyse(sections, subject_hint=subject_hint)

        prompt = f"""Here are passages from a student's own study material for "{subject_hint or 'their subject'}".

{_context(sections)}

Identify the concepts this material teaches, in the order they should be learned.

Return JSON only:
{{"subject_summary": "two sentences on what this material covers",
 "concepts": [
   {{"name": "short concept name",
     "summary": "two sentences explaining it, for a student",
     "definition": "one precise sentence",
     "stage": "foundations|core|application|higher_order",
     "difficulty": 1,
     "terminology": ["term"],
     "formulas": ["only if present in the material"],
     "facts": ["a fact worth knowing"],
     "examples": ["an example, ideally from the material"],
     "misconceptions": ["a way students get this wrong"],
     "prerequisites": ["names of concepts in this list that must come first"],
     "source_ids": ["the [id] of each passage this came from"]}}
 ]}}

Rules: between 3 and 14 concepts. Every concept must come from the passages.
prerequisites must reference names in this same list and must not form a cycle."""

        try:
            data = self._ask(prompt, max_tokens=4000, temperature=0.3)
        except (ProviderUnavailable, ValueError, json.JSONDecodeError):
            return base
        if not isinstance(data, dict) or not isinstance(data.get("concepts"), list):
            return base

        valid_ids = {s.id for s in sections}
        concepts: list[Concept] = []
        by_name: dict[str, Concept] = {}

        for index, raw in enumerate(data["concepts"][:14]):
            if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
                continue
            try:
                stage = PlanStage(str(raw.get("stage", "core")).lower())
            except ValueError:
                stage = PlanStage.CORE
            concept = Concept(
                name=str(raw["name"]).strip()[:60],
                summary=str(raw.get("summary", "")).strip(),
                definition=str(raw.get("definition", "")).strip(),
                stage=stage,
                difficulty=max(1, min(4, int(raw.get("difficulty", 2) or 2))),
                ordinal=index,
                terminology=_strings(raw.get("terminology"), 6),
                formulas=_strings(raw.get("formulas"), 4),
                facts=_strings(raw.get("facts"), 6),
                examples=_strings(raw.get("examples"), 3),
                misconceptions=_strings(raw.get("misconceptions"), 3),
                section_ids=[s for s in _strings(raw.get("source_ids"), 6) if s in valid_ids],
            )
            if not concept.section_ids:
                # Fall back to the on-device grounding rather than lose the link.
                match = self._fallback._sections_about(concept.name, sections)
                concept.section_ids = [s.id for s in match]
            concepts.append(concept)
            by_name[concept.name.lower()] = concept

        if not concepts:
            return base

        # Resolve prerequisite names to ids, dropping anything that would loop.
        for index, raw in enumerate(data["concepts"][: len(concepts)]):
            if not isinstance(raw, dict):
                continue
            concept = concepts[index]
            for name in _strings(raw.get("prerequisites"), 3):
                other = by_name.get(name.strip().lower())
                if other and other.id != concept.id and other.ordinal < concept.ordinal:
                    concept.prerequisites.append(other.id)

        return KnowledgeModel(
            concepts=concepts,
            subject_summary=str(data.get("subject_summary", "")).strip() or base.subject_summary,
            suggested_title=subject_hint or base.suggested_title,
        )

    # -- lesson writing ----------------------------------------------------

    def write_lesson(
        self,
        concept: Concept,
        sections: list[ResourceSection],
        *,
        style: str = "balanced",
        prior_concepts: list[Concept] | None = None,
    ) -> LessonDraft:
        prior = [c.name for c in (prior_concepts or []) if c.id in concept.prerequisites]
        style_note = {
            "concrete": "This student learns best from concrete examples. Lead with them.",
            "formal": "This student prefers precise, formal treatment. Do not over-simplify.",
        }.get(style, "Balance precise explanation with concrete examples.")

        prompt = f"""Write a spoken lesson teaching one concept to a student who is listening.

CONCEPT: {concept.name}
{f"WHAT THEY ALREADY KNOW: {', '.join(prior)}" if prior else "This is early in their course."}
STYLE: {style_note}

THEIR MATERIAL:
{_context(sections, 8000)}

Structure it the way this concept deserves. A typical shape is context, explanation,
example, a question, deeper explanation, recap, then a closing thought, but adapt it.
Include two or three questions of kind "check" spread through, not all at the end.

Return JSON only:
{{"title": "{concept.name}",
 "subtitle": "one short line under the title",
 "estimated_minutes": 8,
 "sections": [
   {{"kind": "context|explanation|example|analogy|check|deeper|application|recap|assessment|next_step",
     "heading": "two or three words, shown on screen",
     "script": "exactly what you say aloud, flowing sentences",
     "source_ids": ["passage ids this draws on"],
     "question": {{"kind": "recall|explanation|application|comparison|reasoning|scenario|misconception_probe|synthesis|teach_back",
                   "prompt": "the question, asked aloud",
                   "expected_points": ["what a good answer contains"],
                   "misconception_markers": ["what a wrong answer would say"],
                   "follow_up": "a nudge if they are stuck"}}
   }}
 ]}}

"question" only on sections of kind "check" or "assessment". Omit it elsewhere."""

        try:
            data = self._ask(prompt, max_tokens=4000, temperature=0.5)
        except (ProviderUnavailable, ValueError, json.JSONDecodeError):
            return self._fallback.write_lesson(
                concept, sections, style=style, prior_concepts=prior_concepts
            )
        if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
            return self._fallback.write_lesson(
                concept, sections, style=style, prior_concepts=prior_concepts
            )

        valid_ids = {s.id for s in sections}
        drafted: list[DraftSection] = []
        for raw in data["sections"][:16]:
            if not isinstance(raw, dict) or not str(raw.get("script", "")).strip():
                continue
            try:
                kind = SectionKind(str(raw.get("kind", "explanation")).lower())
            except ValueError:
                kind = SectionKind.EXPLANATION
            question = None
            raw_question = raw.get("question")
            if kind in (SectionKind.CHECK, SectionKind.ASSESSMENT) and isinstance(raw_question, dict):
                question = self._question_from(raw_question, concept)
            if kind in (SectionKind.CHECK, SectionKind.ASSESSMENT) and question is None:
                kind = SectionKind.EXPLANATION
            drafted.append(
                DraftSection(
                    kind=kind,
                    heading=str(raw.get("heading", "")).strip()[:40],
                    script=str(raw["script"]).strip(),
                    source_section_ids=[s for s in _strings(raw.get("source_ids"), 6) if s in valid_ids],
                    question=question,
                )
            )

        if not drafted:
            return self._fallback.write_lesson(
                concept, sections, style=style, prior_concepts=prior_concepts
            )

        return LessonDraft(
            title=str(data.get("title", concept.name)).strip() or concept.name,
            subtitle=str(data.get("subtitle", "")).strip(),
            sections=drafted,
            estimated_minutes=max(3, min(30, int(data.get("estimated_minutes", 8) or 8))),
        )

    def _question_from(self, raw: dict[str, object], concept: Concept) -> Question | None:
        prompt = str(raw.get("prompt", "")).strip()
        if not prompt:
            return None
        try:
            kind = QuestionKind(str(raw.get("kind", "explanation")).lower())
        except ValueError:
            kind = QuestionKind.EXPLANATION
        return Question(
            space_id=concept.space_id,
            concept_id=concept.id,
            kind=kind,
            prompt=prompt,
            expected_points=_strings(raw.get("expected_points"), 5) or [concept.name],
            misconception_markers=_strings(raw.get("misconception_markers"), 3),
            follow_up=str(raw.get("follow_up", "")).strip(),
            difficulty=concept.difficulty,
        )

    def write_questions(
        self, concept: Concept, sections: list[ResourceSection], *, count: int = 3
    ) -> list[Question]:
        prompt = f"""Write {count} questions on "{concept.name}" for a student to answer OUT LOUD.

THEIR MATERIAL:
{_context(sections, 6000)}

Vary the kinds. Nothing multiple-choice, nothing answerable with one word.
Good forms: explain in your own words, why does this happen, what would change if,
how do these connect, teach it back to me.

Return JSON only:
[{{"kind": "recall|explanation|application|comparison|reasoning|scenario|misconception_probe|synthesis|teach_back",
   "prompt": "the question",
   "expected_points": ["what a good answer contains"],
   "misconception_markers": ["what a wrong answer would say"],
   "follow_up": "a nudge if they are stuck"}}]"""
        try:
            data = self._ask(prompt, max_tokens=1600, temperature=0.6)
        except (ProviderUnavailable, ValueError, json.JSONDecodeError):
            return self._fallback.write_questions(concept, sections, count=count)
        if not isinstance(data, list):
            return self._fallback.write_questions(concept, sections, count=count)

        questions = [
            q for q in (self._question_from(raw, concept) for raw in data if isinstance(raw, dict))
            if q is not None
        ]
        return questions[:count] or self._fallback.write_questions(concept, sections, count=count)

    # -- evaluation and conversation ---------------------------------------

    def evaluate_answer(
        self, transcript: str, question: Question, concept: Concept | None = None
    ) -> AnswerAnalysis:
        prompt = f"""A student answered a spoken question. Judge what they understand.

QUESTION: {question.prompt}
WHAT A GOOD ANSWER CONTAINS: {json.dumps(question.expected_points)}
KNOWN WRONG TURNS: {json.dumps(question.misconception_markers)}

THEIR SPOKEN ANSWER (transcribed, so expect loose grammar): "{transcript}"

Judge the understanding, not the phrasing. Transcription errors are not mistakes.
If the answer is too short or vague to tell, say so with "unclear" rather than guessing.

Return JSON only:
{{"judgement": "correct|mostly_correct|incomplete|misconception|incorrect|unclear",
  "confidence": 0.0,
  "matched_points": ["points they covered"],
  "missed_points": ["points they missed"],
  "detected_misconceptions": ["wrong turns they took"],
  "reason": "one sentence, for the teacher not the student"}}"""
        try:
            data = self._ask(prompt, max_tokens=800, temperature=0.2)
        except (ProviderUnavailable, ValueError, json.JSONDecodeError):
            return self._fallback.evaluate_answer(transcript, question, concept)
        if not isinstance(data, dict):
            return self._fallback.evaluate_answer(transcript, question, concept)

        try:
            judgement = Judgement(str(data.get("judgement", "unclear")).lower())
        except ValueError:
            judgement = Judgement.UNCLEAR

        matched = _strings(data.get("matched_points"), 6)
        total = len(question.expected_points) or 1
        analysis = AnswerAnalysis(
            judgement=judgement,
            confidence=_as_float(data.get("confidence"), 0.6),
            matched_points=matched,
            missed_points=_strings(data.get("missed_points"), 6),
            detected_misconceptions=_strings(data.get("detected_misconceptions"), 4),
            coverage=min(1.0, len(matched) / total),
            reason=str(data.get("reason", "")).strip(),
        )
        # A model is not exempt from the guard rails.
        return sanity_check(analysis, transcript)

    def answer_question(
        self,
        question: str,
        *,
        concept: Concept | None,
        sections: list[ResourceSection],
        history: list[tuple[str, str]] | None = None,
    ) -> GroundedAnswer:
        recent = ""
        if history:
            turns = "\n".join(f"{role}: {text}" for role, text in history[-6:])
            recent = f"\nWHAT WE HAVE BEEN DISCUSSING:\n{turns}\n"

        prompt = f"""The student asked: "{question}"
{f'We are working on: {concept.name}' if concept else ''}{recent}
THEIR MATERIAL:
{_context(sections, 8000)}

Answer out loud, in flowing sentences, grounded in their material.
If their material does not cover it, say so and answer from general knowledge,
making that distinction audible in the answer itself.

Return JSON only:
{{"answer": "what you say",
  "source_ids": ["passage ids you used"],
  "used_outside_knowledge": false}}"""
        try:
            data = self._ask(prompt, max_tokens=1200, temperature=0.4)
        except (ProviderUnavailable, ValueError, json.JSONDecodeError):
            return self._fallback.answer_question(
                question, concept=concept, sections=sections, history=history
            )
        if not isinstance(data, dict) or not str(data.get("answer", "")).strip():
            return self._fallback.answer_question(
                question, concept=concept, sections=sections, history=history
            )

        valid_ids = {s.id for s in sections}
        used = [s for s in _strings(data.get("source_ids"), 5) if s in valid_ids]
        heading = next((s.heading for s in sections if s.id in used and s.heading), "")
        return GroundedAnswer(
            text=str(data["answer"]).strip(),
            source_section_ids=used,
            source_note=f"From {heading}" if heading else ("From your uploaded material" if used else ""),
            used_outside_knowledge=bool(data.get("used_outside_knowledge")),
        )

    def reexplain(
        self,
        concept: Concept,
        sections: list[ResourceSection],
        *,
        mode: str = "simpler",
    ) -> GroundedAnswer:
        instruction = {
            "simpler": "Explain it again in much plainer language, as if to someone meeting it for the first time.",
            "different": "Explain it again from a completely different angle. Do not repeat your earlier framing.",
            "example": "Give a fresh concrete example that makes it click. Walk through it.",
        }.get(mode, "Explain it again more simply.")

        prompt = f"""The student is not following "{concept.name}" yet.

{instruction}

THEIR MATERIAL:
{_context(sections, 6000)}

Speak directly to them, out loud, in flowing sentences. Do not apologise and do not
tell them it is difficult. Just explain it better.

Return JSON only: {{"answer": "what you say", "source_ids": ["passage ids used"]}}"""
        try:
            data = self._ask(prompt, max_tokens=1200, temperature=0.6)
        except (ProviderUnavailable, ValueError, json.JSONDecodeError):
            return self._fallback.reexplain(concept, sections, mode=mode)
        if not isinstance(data, dict) or not str(data.get("answer", "")).strip():
            return self._fallback.reexplain(concept, sections, mode=mode)

        valid_ids = {s.id for s in sections}
        return GroundedAnswer(
            text=str(data["answer"]).strip(),
            source_section_ids=[s for s in _strings(data.get("source_ids"), 5) if s in valid_ids],
            source_note="From your uploaded material",
        )


def _strings(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:limit]


def _as_float(value: object, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
