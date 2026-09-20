"""The on-device teaching engine.

This runs with no API key, no network and no model. It reads the student's
actual uploaded material, finds the concepts in it, orders them by what depends
on what, and writes narration that teaches each one.

What it genuinely does:
  - extracts concepts from headings, definitions and recurring technical terms
  - infers prerequisites from which concepts are mentioned inside which others
  - grounds every sentence it narrates in a passage it can point at
  - writes questions whose expected answers come from the source text
  - reads spoken answers with the lexical scorer in app/domain/assessment.py

What it does not do: invent facts. Every claim it narrates is drawn from the
uploaded material or is scaffolding language around it ("Before we move on,
think about why that happens"). That is a real limit, and the honest reason to
set ANTHROPIC_API_KEY when you want richer analogies and freer explanation.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict

from ...domain.assessment import AnswerAnalysis, evaluate as lexical_evaluate, stem
from ...domain.models import (
    Concept,
    PlanStage,
    Question,
    QuestionKind,
    ResourceSection,
    SectionKind,
)
from .. import textanalysis as ta
from .base import (
    DraftSection,
    GroundedAnswer,
    KnowledgeModel,
    LessonDraft,
)

#: Roughly how fast the narration is read back, for duration estimates.
WORDS_PER_MINUTE = 150


def _concept_key(name: str) -> str:
    """Canonical identity of a concept name, used for merging duplicates."""
    return " ".join(stem(w) for w in ta.content_words(name))


def _key_overlap(left: str, right: str) -> float:
    """How much two concept keys share, relative to the smaller of them."""
    a, b = set(left.split()), set(right.split())
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _spoken_name(name: str) -> str:
    """A concept name as it should be said inside a sentence.

    Titles arrive in Title Case from headings, which reads wrong mid-sentence.
    Lowercasing everything would mangle the eponymous laws that fill science
    syllabuses, so possessives and acronyms keep their capitals.
    """
    spoken: list[str] = []
    for word in name.split():
        bare = word.strip(".,")
        is_possessive = "'" in bare and bare.lower().endswith(("'s", "s'"))
        is_acronym = len(bare) > 1 and bare.isupper()
        spoken.append(word if (is_possessive or is_acronym) else word.lower())
    return " ".join(spoken)


def _key_phrase(sentence: str, *, max_words: int = 14) -> str:
    """The part of a sentence that carries the idea.

    Expected answers are matched lexically, so a whole textbook sentence is a
    poor target: most of its words are scaffolding a student would never
    reproduce when explaining the idea aloud. This keeps the clause that
    matters and drops the lead-in.
    """
    text = " ".join(sentence.split()).rstrip(".")
    # Drop a leading clause that only points at the subject.
    for marker in (", which means ", ", so ", ", because ", ", and this ", ": "):
        index = text.lower().find(marker)
        if 0 < index < len(text) - len(marker) - 10:
            text = text[index + len(marker):]
            break
    text = re.sub(
        r"^(this|that|it|these|they|the law|the rule)\s+(is|are|means|states|says|tells us)\s+(that\s+)?",
        "", text, flags=re.I,
    )
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words])


def _tidy_formula(formula: str) -> str:
    """Cut a formula back to the relationship itself.

    Extraction often trails into the prose that follows it, so
    "F = m a, where F is the resultant force" becomes "F = m a".
    """
    cleaned = " ".join(formula.split())
    for marker in (", where", " where ", ", and ", ", with", ", in which", ";"):
        index = cleaned.lower().find(marker.lower())
        if index > 0:
            cleaned = cleaned[:index]
    return cleaned.strip(" .,:;")


def _stable_choice(seed: str, options: list[str]) -> str:
    """Pick deterministically from `options`, so a lesson reads the same twice."""
    if not options:
        return ""
    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    return options[digest[0] % len(options)]


def _sentence_case(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    return text[0].upper() + text[1:]


def _as_spoken(text: str) -> str:
    """Tidy extracted text so it reads aloud without sounding like a document."""
    spoken = " ".join(text.split())
    spoken = re.sub(r"\[\d+\]|\(\d{4}\)", "", spoken)          # citations
    spoken = re.sub(r"\bFig(ure)?\.?\s*\d+[a-z]?\b", "the diagram", spoken, flags=re.I)
    spoken = re.sub(r"\bTable\s*\d+\b", "the table", spoken, flags=re.I)
    spoken = re.sub(r"\bp\.\s*\d+\b", "", spoken)
    spoken = re.sub(r"\s+([,.;:])", r"\1", spoken)
    spoken = re.sub(r"\s{2,}", " ", spoken)
    return spoken.strip()


def _titlecase_term(term: str) -> str:
    small = {"of", "the", "and", "in", "on", "to", "a", "an", "for", "with"}
    parts = term.split()
    return " ".join(
        p.capitalize() if i == 0 or p.lower() not in small else p.lower()
        for i, p in enumerate(parts)
    )


class OnDeviceEngine:
    """A `TeachingEngine` built from text analysis rather than a model."""

    name = "ondevice"
    display_name = "On-device teacher"
    grounded_only = True

    # -- analysis ----------------------------------------------------------

    def analyse(
        self, sections: list[ResourceSection], *, subject_hint: str = ""
    ) -> KnowledgeModel:
        if not sections:
            return KnowledgeModel(concepts=[], subject_summary="", suggested_title=subject_hint)

        passages = [s.text for s in sections]
        headings = [s.heading for s in sections if s.heading]
        terms = ta.extract_terms(passages, headings=headings, limit=48)

        findings_by_section = {s.id: ta.analyse_passage(s.text) for s in sections}

        # Definitions found anywhere in the material, keyed by the term defined.
        definitions: dict[str, tuple[str, str]] = {}
        for section in sections:
            for term, sentence in findings_by_section[section.id].definitions:
                key = " ".join(stem(w) for w in term.lower().split())
                definitions.setdefault(key, (term, sentence))

        candidates = self._candidate_concepts(
            sections, terms, definitions, headings, subject_hint=subject_hint
        )
        concepts = self._build_concepts(candidates, sections, findings_by_section, definitions)
        self._infer_prerequisites(concepts)
        self._assign_stages(concepts)

        summary = ta.summarise(" ".join(passages[:6]), max_sentences=2)
        title = subject_hint or self._infer_title(headings, concepts)
        return KnowledgeModel(concepts=concepts, subject_summary=summary, suggested_title=title)

    def _candidate_concepts(
        self,
        sections: list[ResourceSection],
        terms: list[ta.Term],
        definitions: dict[str, tuple[str, str]],
        headings: list[str],
        *,
        subject_hint: str = "",
    ) -> list[tuple[str, float]]:
        """Rank candidate concept names from headings, definitions and terms."""
        scored: dict[str, float] = defaultdict(float)
        display: dict[str, str] = {}
        from_heading: set[str] = set()
        # The document's own title is the name of the subject, not a concept
        # inside it, so it must not become a lesson.
        hint_key = _concept_key(subject_hint) if subject_hint else ""

        def add(name: str, weight: float, *, heading: bool = False) -> None:
            name = name.strip(" .:-–—")
            if not (2 < len(name) <= 60):
                return
            if len(name.split()) > 6:
                return
            key = _concept_key(name)
            if not key or (hint_key and _key_overlap(key, hint_key) >= 0.75):
                return
            scored[key] += weight
            if heading:
                from_heading.add(key)
            # Prefer the most readable rendering seen for this key.
            if key not in display or len(name) < len(display[key]):
                display[key] = name

        # Headings are the author's own view of the structure. Trust them most.
        seen_headings: set[str] = set()
        for heading in headings:
            cleaned = ta.clean_heading(heading)
            if cleaned.lower() in seen_headings:
                continue
            seen_headings.add(cleaned.lower())
            add(cleaned, 10.0, heading=True)

        for _key, (term, _sentence) in definitions.items():
            add(term, 6.0)

        for term in terms[:30]:
            add(term.text, min(5.0, term.score / 12.0) + (2.0 if term.in_heading else 0.0))

        # A concept the author gave a heading to always outranks one inferred
        # from vocabulary, however often that vocabulary recurs.
        ranked = sorted(
            scored.items(),
            key=lambda kv: (kv[0] in from_heading, kv[1] + (4.0 if kv[0] in definitions else 0.0)),
            reverse=True,
        )
        return [(display[k], v) for k, v in ranked if k in display]

    def _build_concepts(
        self,
        candidates: list[tuple[str, float]],
        sections: list[ResourceSection],
        findings_by_section: dict[str, ta.Findings],
        definitions: dict[str, tuple[str, str]],
    ) -> list[Concept]:
        """Attach source passages and findings to each surviving candidate."""
        # Enough lessons to cover the material. When the author supplied
        # headings, that count is the floor: every heading is a topic they
        # thought was worth separating.
        heading_count = len({ta.clean_heading(s.heading).lower() for s in sections if s.heading})
        target = max(3, min(16, max(heading_count, round(len(sections) / 2.2) + 2)))

        concepts: list[Concept] = []
        used_keys: set[str] = set()
        claimed: set[str] = set()

        for name, _score in candidates:
            if len(concepts) >= target:
                break
            key = _concept_key(name)
            if key in used_keys or self._too_similar(key, used_keys):
                continue

            matches = self._sections_about(name, sections)
            if not matches:
                continue
            # Spread across the material: a later candidate whose passages are
            # all spoken for is a different name for a lesson already written.
            if all(s.id in claimed for s in matches) and concepts:
                continue
            used_keys.add(key)
            claimed.update(s.id for s in matches[:1])

            facts: list[str] = []
            formulas: list[str] = []
            examples: list[str] = []
            misconceptions: list[str] = []
            for section in matches:
                found = findings_by_section[section.id]
                facts.extend(found.facts)
                formulas.extend(found.formulas)
                examples.extend(found.examples)
                misconceptions.extend(found.misconceptions)

            # Only accept a definition that is genuinely about this concept.
            # A loose match hands "Forces and Motion" the definition of
            # something else entirely, which is worse than having none.
            definition = ""
            entry = definitions.get(key)
            if entry:
                definition = _as_spoken(entry[1])
            else:
                best_overlap = 0.0
                for section in matches:
                    for term, sentence in findings_by_section[section.id].definitions:
                        overlap = _key_overlap(_concept_key(term), key)
                        if overlap > best_overlap and overlap >= 0.5:
                            best_overlap = overlap
                            definition = _as_spoken(sentence)

            body = " ".join(s.text for s in matches)
            summary = ta.summarise(body, max_sentences=2)

            concepts.append(
                Concept(
                    name=_titlecase_term(name),
                    summary=_as_spoken(summary),
                    definition=definition,
                    difficulty=ta.readability_difficulty(body),
                    ordinal=min(s.ordinal for s in matches),
                    terminology=self._terminology(body, name),
                    formulas=_dedupe([_tidy_formula(f) for f in formulas if _tidy_formula(f)])[:4],
                    # Ranked so the most substantive facts lead: these become
                    # the expected answers to questions, and a sentence that
                    # merely says what the idea is about makes a poor target.
                    facts=[_as_spoken(f) for f in ta.rank_by_informativeness(_dedupe(facts))[:6]],
                    examples=[_as_spoken(e) for e in _dedupe(examples)[:3]],
                    misconceptions=[_as_spoken(m) for m in _dedupe(misconceptions)[:2]],
                    section_ids=[s.id for s in matches],
                )
            )

        concepts.sort(key=lambda c: c.ordinal)
        for index, concept in enumerate(concepts):
            concept.ordinal = index
        return concepts

    @staticmethod
    def _too_similar(key: str, used: set[str]) -> bool:
        """Reject a candidate that is a near-duplicate of one already taken."""
        return any(_key_overlap(key, existing) >= 0.75 for existing in used)

    @staticmethod
    def _sections_about(name: str, sections: list[ResourceSection]) -> list[ResourceSection]:
        """Passages that are genuinely *about* this concept, best first.

        Mentioning a word is not the same as being about it. A physics chapter
        says "force" on every page, so a passage only counts when the concept
        is in its heading or appears densely enough to be the subject.
        """
        needles = [stem(w) for w in ta.content_words(name)]
        if not needles:
            return []

        scored: list[tuple[float, ResourceSection]] = []
        for section in sections:
            body = [stem(w) for w in ta.content_words(section.text)]
            if not body:
                continue
            heading_tokens = {stem(w) for w in ta.content_words(section.heading)}
            present = set(body)

            hits = sum(1 for n in needles if n in present)
            if not hits:
                continue
            coverage = hits / len(needles)
            occurrences = sum(body.count(n) for n in needles)
            density = occurrences / max(1, len(body)) * 100  # per 100 content words

            heading_match = bool(needles) and all(n in heading_tokens for n in needles)
            score = coverage + min(2.0, density / 2.0) + (3.0 if heading_match else 0.0)

            # Without a heading match, the concept has to actually carry the
            # passage rather than be name-dropped in it.
            if not heading_match and (coverage < 1.0 or density < 1.2):
                continue
            scored.append((score, section))

        scored.sort(key=lambda pair: (-pair[0], pair[1].ordinal))
        return [section for _score, section in scored][:3]

    @staticmethod
    def _terminology(body: str, name: str) -> list[str]:
        terms = ta.extract_terms([body], limit=8)
        own = {stem(w) for w in ta.content_words(name)}
        return [
            t.text for t in terms
            if t.count >= 2 and not own.issuperset({stem(w) for w in t.text.split()})
        ][:5]

    @staticmethod
    def _infer_prerequisites(concepts: list[Concept]) -> None:
        """B is a prerequisite of A when A's passages lean on B's vocabulary.

        Only edges that run from a more foundational concept to a less
        foundational one are kept, which is what makes the graph acyclic.
        """
        if len(concepts) < 2:
            return
        # Foundationality: how early it appears and how widely it is referenced.
        mentions: dict[str, int] = defaultdict(int)
        tokens_by_id = {
            c.id: {stem(w) for w in ta.content_words(c.name)} for c in concepts
        }
        text_by_id = {
            c.id: {stem(w) for w in ta.content_words(f"{c.summary} {c.definition} {' '.join(c.facts)}")}
            for c in concepts
        }
        for concept in concepts:
            for other in concepts:
                if other.id == concept.id:
                    continue
                if tokens_by_id[other.id] and tokens_by_id[other.id] <= text_by_id[concept.id]:
                    mentions[other.id] += 1

        def foundation_score(concept: Concept) -> float:
            return mentions[concept.id] * 2.0 - concept.ordinal * 0.5 - concept.difficulty * 0.4

        # A meaningful gap in foundationality, so a passing mention does not
        # become a dependency.
        margin = 1.5

        for concept in concepts:
            prerequisites: list[tuple[float, str]] = []
            for other in concepts:
                if other.id == concept.id:
                    continue
                if not tokens_by_id[other.id]:
                    continue
                referenced = tokens_by_id[other.id] <= text_by_id[concept.id]
                if not referenced:
                    continue
                if foundation_score(other) < foundation_score(concept) + margin:
                    continue
                if other.ordinal > concept.ordinal:
                    continue
                prerequisites.append((foundation_score(other), other.id))
            prerequisites.sort(reverse=True)
            # One prerequisite only. Long chains push late concepts to the end
            # of the plan for reasons a student would not recognise, and the
            # single strongest dependency is the one worth naming aloud.
            concept.prerequisites = [cid for _score, cid in prerequisites[:1]]

    @staticmethod
    def _assign_stages(concepts: list[Concept]) -> None:
        """Spread concepts across the teaching stages by depth and demand."""
        if not concepts:
            return
        from ...domain.curriculum import prerequisite_depth

        depth = prerequisite_depth(concepts)
        max_depth = max(depth.values()) if depth else 0
        count = len(concepts)
        for index, concept in enumerate(concepts):
            position = index / max(1, count - 1) if count > 1 else 0.0
            own_depth = depth.get(concept.id, 0)
            if own_depth == 0 and position < 0.34:
                concept.stage = PlanStage.FOUNDATIONS
            elif position < 0.66 or own_depth < max(1, max_depth):
                concept.stage = PlanStage.CORE
            elif concept.difficulty >= 3:
                concept.stage = PlanStage.HIGHER_ORDER
            else:
                concept.stage = PlanStage.APPLICATION

    @staticmethod
    def _infer_title(headings: list[str], concepts: list[Concept]) -> str:
        if headings:
            return ta.clean_heading(headings[0])[:60]
        if concepts:
            return concepts[0].name
        return "Your subject"

    # -- lesson writing ----------------------------------------------------

    def write_lesson(
        self,
        concept: Concept,
        sections: list[ResourceSection],
        *,
        style: str = "balanced",
        prior_concepts: list[Concept] | None = None,
    ) -> LessonDraft:
        """Write a lesson as a sequence of narrated beats.

        The structure adapts: a concept with a formula gets a worked beat, one
        with a recorded misconception gets a warning beat, a first-in-subject
        concept gets a gentler opening than one that builds on earlier work.
        """
        source_ids = [s.id for s in sections] or concept.section_ids
        seed = concept.id + concept.name
        prior = [c for c in (prior_concepts or []) if c.id in concept.prerequisites]
        drafted: list[DraftSection] = []

        drafted.append(self._context_beat(concept, prior, seed, source_ids))
        drafted.append(self._explanation_beat(concept, seed, source_ids, style))

        if concept.examples:
            drafted.append(self._example_beat(concept, seed, source_ids))
        else:
            drafted.append(self._analogy_beat(concept, seed, source_ids))

        questions = self.write_questions(concept, sections, count=3)
        if questions:
            drafted.append(
                DraftSection(
                    SectionKind.CHECK,
                    "A question for you",
                    self._check_preamble(seed) + " " + questions[0].prompt,
                    source_ids,
                    questions[0],
                )
            )

        if concept.formulas:
            drafted.append(self._formula_beat(concept, source_ids))
        if concept.misconceptions:
            drafted.append(self._misconception_beat(concept, source_ids))

        if concept.facts:
            drafted.append(self._deeper_beat(concept, seed, source_ids))

        if len(questions) > 1:
            drafted.append(
                DraftSection(
                    SectionKind.CHECK,
                    "One more",
                    questions[1].prompt,
                    source_ids,
                    questions[1],
                )
            )

        drafted.append(self._recap_beat(concept, seed, source_ids))

        if len(questions) > 2:
            drafted.append(
                DraftSection(
                    SectionKind.ASSESSMENT,
                    "Before we finish",
                    questions[2].prompt,
                    source_ids,
                    questions[2],
                )
            )

        drafted.append(
            DraftSection(
                SectionKind.NEXT_STEP,
                "Where this goes",
                self._next_step_script(concept),
                source_ids,
            )
        )

        total_words = sum(len(d.script.split()) for d in drafted)
        minutes = max(4, round(total_words / WORDS_PER_MINUTE) + len(
            [d for d in drafted if d.kind is SectionKind.CHECK]
        ))

        return LessonDraft(
            title=concept.name,
            subtitle=self._subtitle(concept),
            sections=drafted,
            estimated_minutes=minutes,
        )

    def _context_beat(
        self, concept: Concept, prior: list[Concept], seed: str, source_ids: list[str]
    ) -> DraftSection:
        spoken = _spoken_name(concept.name)
        opener = _stable_choice(seed + "open", [
            f"Let us look at {spoken}.",
            f"This one is about {spoken}.",
            f"Next up is {spoken}.",
        ])
        if prior:
            names = " and ".join(_spoken_name(c.name) for c in prior[:2])
            bridge = (
                f" You already have {names} in hand, and this builds directly on that."
            )
        else:
            bridge = " We are starting from the beginning here, so nothing is assumed."
        orientation = _stable_choice(seed + "orient", [
            " I will explain it, give you something concrete to hold onto, and then ask you about it.",
            " I will talk you through it first, and then check what landed.",
            " We will take it slowly, and there is no hurry at any point.",
        ])
        return DraftSection(SectionKind.CONTEXT, "Where we are", opener + bridge + orientation, source_ids)

    def _explanation_beat(
        self, concept: Concept, seed: str, source_ids: list[str], style: str
    ) -> DraftSection:
        parts: list[str] = []
        if concept.definition:
            lead = _stable_choice(seed + "def", [
                "Your material puts it like this.",
                "Here is how it is defined in your notes.",
                "The definition you were given is this.",
            ])
            parts.append(f"{lead} {_sentence_case(concept.definition)}")
            parts.append(_stable_choice(seed + "unpack", [
                "Let me unpack that, because a definition rarely does the work on its own.",
                "That is precise, but precision is not the same as clarity. Let us take it apart.",
                "Read quickly that can slide past you, so here it is again in plainer words.",
            ]))
        if concept.summary:
            parts.append(_sentence_case(concept.summary))
        if not parts:
            parts.append(
                f"{concept.name} is one of the ideas your material keeps returning to."
            )
        if concept.terminology:
            terms = ", ".join(concept.terminology[:3])
            parts.append(
                f"Some words travel with this idea, and you will hear them throughout: {terms}."
            )
        if style == "concrete":
            parts.append("I will keep this as concrete as I can, and lean on examples over definitions.")
        return DraftSection(SectionKind.EXPLANATION, "The idea itself", " ".join(parts), source_ids)

    def _example_beat(self, concept: Concept, seed: str, source_ids: list[str]) -> DraftSection:
        lead = _stable_choice(seed + "ex", [
            "Here is the example your material uses.",
            "Your notes work through a case, and it is worth walking.",
            "An example will fix this faster than more explanation.",
        ])
        body = " ".join(_sentence_case(e) for e in concept.examples[:2])
        close = _stable_choice(seed + "exclose", [
            " Notice what is doing the work there. That is the part to carry forward.",
            " Hold onto the shape of that. The details change, the shape does not.",
            " If you can rebuild that example yourself later, you have the idea.",
        ])
        return DraftSection(SectionKind.EXAMPLE, "An example", f"{lead} {body}{close}", source_ids)

    def _analogy_beat(self, concept: Concept, seed: str, source_ids: list[str]) -> DraftSection:
        name = _spoken_name(concept.name)
        script = _stable_choice(seed + "an", [
            f"Your material does not give a worked example of {name}, so let us make the idea concrete a "
            f"different way. Picture explaining {name} to someone who has never met it. What is the one "
            f"sentence you would lead with? That sentence is the idea, and everything else is detail hanging off it.",
            f"There is no worked example of {name} in your notes, so try this instead. Think of the last "
            f"time you saw {name} referred to without being explained. What was assumed? Whatever you had to "
            f"supply yourself is exactly what this concept is.",
            f"Rather than an example, try a test. If {name} were removed from your subject entirely, what would "
            f"stop making sense? What breaks tells you what the idea is holding up.",
        ])
        return DraftSection(SectionKind.ANALOGY, "Another way in", script, source_ids)

    @staticmethod
    def _check_preamble(seed: str) -> str:
        return _stable_choice(seed + "chk", [
            "Before we move on, think about this for a moment.",
            "Let us test that idea.",
            "Here is where I hand it to you.",
        ])

    def _formula_beat(self, concept: Concept, source_ids: list[str]) -> DraftSection:
        formulas = "; ".join(concept.formulas[:2])
        script = (
            f"There is a relationship written down in your material: {formulas}. "
            "Do not memorise it as a string of letters. Read it as a sentence about what depends on what, "
            "and it will stay with you far longer."
        )
        return DraftSection(SectionKind.DEEPER, "The relationship", script, source_ids)

    def _misconception_beat(self, concept: Concept, source_ids: list[str]) -> DraftSection:
        warning = " ".join(_sentence_case(m) for m in concept.misconceptions[:2])
        script = (
            f"Your material flags a place people go wrong here, so it is worth naming. {warning} "
            "If that is where your thinking was heading, that is useful to know now rather than later."
        )
        return DraftSection(SectionKind.DEEPER, "A place people trip", script, source_ids)

    def _deeper_beat(self, concept: Concept, seed: str, source_ids: list[str]) -> DraftSection:
        lead = _stable_choice(seed + "deep", [
            "A few things follow from this that are worth having.",
            "There is more in your material on this, and it repays a second pass.",
            "Once the core idea sits still, these details start to matter.",
        ])
        body = " ".join(_sentence_case(f) for f in concept.facts[:3])
        return DraftSection(SectionKind.APPLICATION, "Going further", f"{lead} {body}", source_ids)

    def _recap_beat(self, concept: Concept, seed: str, source_ids: list[str]) -> DraftSection:
        lead = _stable_choice(seed + "recap", [
            "Let us gather that up.",
            "Here is what to take away.",
            "Stepping back, this is the shape of it.",
        ])
        core = concept.definition or concept.summary or f"{concept.name} is the idea we have been working on."
        takeaway = f" The centre of it is this: {_sentence_case(_as_spoken(core))}"
        if concept.terminology:
            takeaway += f" And the vocabulary that travels with it: {', '.join(concept.terminology[:3])}."
        return DraftSection(SectionKind.RECAP, "Taking stock", lead + takeaway, source_ids)

    def _next_step_script(self, concept: Concept) -> str:
        return (
            f"That is {_spoken_name(concept.name)}. If it feels settled, we can move on whenever you are ready. "
            "If any part of it is still loose, say so and I will come at it from a different angle. "
            "There is no clock on this."
        )

    @staticmethod
    def _subtitle(concept: Concept) -> str:
        if concept.definition:
            words = concept.definition.split()
            return _as_spoken(" ".join(words[:14])) + ("..." if len(words) > 14 else "")
        if concept.summary:
            words = concept.summary.split()
            return _as_spoken(" ".join(words[:14])) + ("..." if len(words) > 14 else "")
        return concept.stage.label

    # -- questions ---------------------------------------------------------

    def write_questions(
        self, concept: Concept, sections: list[ResourceSection], *, count: int = 3
    ) -> list[Question]:
        """Questions that can be answered out loud, in mixed forms."""
        questions: list[Question] = []
        name = concept.name
        lower = _spoken_name(name)
        seed = concept.id

        def expected_from(text: str, limit: int = 3) -> list[str]:
            found = [s for s in ta.sentences(text) if len(s.split()) >= 5]
            return [_key_phrase(_as_spoken(s)) for s in found[:limit]]

        if concept.definition:
            questions.append(
                Question(
                    space_id=concept.space_id,
                    concept_id=concept.id,
                    kind=QuestionKind.EXPLANATION,
                    prompt=_stable_choice(seed + "q1", [
                        f"In your own words, what is {lower}?",
                        f"How would you explain {lower} to someone who has not met it?",
                        f"Put {lower} into your own words for me.",
                    ]),
                    expected_points=expected_from(concept.definition, 2) or [concept.definition],
                    misconception_markers=concept.misconceptions[:1],
                    follow_up=f"Say a little more about what makes {lower} different from what surrounds it.",
                    difficulty=max(1, concept.difficulty - 1),
                )
            )

        if concept.facts:
            questions.append(
                Question(
                    space_id=concept.space_id,
                    concept_id=concept.id,
                    kind=QuestionKind.REASONING,
                    prompt=_stable_choice(seed + "q2", [
                        f"Why does {lower} work the way it does?",
                        f"What is the reason behind {lower}?",
                        f"Why is {lower} true, rather than just something to remember?",
                    ]),
                    expected_points=[_key_phrase(_as_spoken(f)) for f in concept.facts[:2]],
                    misconception_markers=concept.misconceptions[:1],
                    follow_up="What would have to change for that to stop being true?",
                    difficulty=concept.difficulty,
                )
            )

        if concept.formulas:
            questions.append(
                Question(
                    space_id=concept.space_id,
                    concept_id=concept.id,
                    kind=QuestionKind.APPLICATION,
                    prompt=(
                        f"Reading {concept.formulas[0]} as a sentence rather than symbols, "
                        "what is it telling you depends on what?"
                    ),
                    expected_points=[concept.formulas[0]]
                    + [_key_phrase(_as_spoken(f)) for f in concept.facts[:1]],
                    follow_up="And what happens to the result if you double one side of it?",
                    difficulty=min(4, concept.difficulty + 1),
                )
            )

        if concept.examples:
            questions.append(
                Question(
                    space_id=concept.space_id,
                    concept_id=concept.id,
                    kind=QuestionKind.SCENARIO,
                    prompt=f"Can you give me a situation where {lower} would matter, in your own words?",
                    expected_points=[_key_phrase(_as_spoken(e)) for e in concept.examples[:2]],
                    follow_up="What would change about that situation if the idea did not hold?",
                    difficulty=min(4, concept.difficulty + 1),
                )
            )

        if concept.misconceptions:
            questions.append(
                Question(
                    space_id=concept.space_id,
                    concept_id=concept.id,
                    kind=QuestionKind.MISCONCEPTION_PROBE,
                    prompt=f"What is the most common way people get {lower} wrong, and why is it wrong?",
                    expected_points=[_key_phrase(_as_spoken(m)) for m in concept.misconceptions[:2]],
                    misconception_markers=[],
                    follow_up="What is the correction you would give them?",
                    difficulty=min(4, concept.difficulty + 1),
                )
            )

        # Always able to offer a teach-back, which works for any concept.
        questions.append(
            Question(
                space_id=concept.space_id,
                concept_id=concept.id,
                kind=QuestionKind.TEACH_BACK,
                prompt=f"Teach {lower} back to me, as if I were the one learning it.",
                expected_points=(
                    expected_from(concept.definition or concept.summary, 2)
                    or [concept.name]
                ),
                follow_up="What would you say first if you only had one sentence?",
                difficulty=min(4, concept.difficulty + 1),
            )
        )

        for question in questions:
            question.lesson_id = ""
        return questions[:count]

    # -- evaluation and conversation ---------------------------------------

    def evaluate_answer(
        self, transcript: str, question: Question, concept: Concept | None = None
    ) -> AnswerAnalysis:
        return lexical_evaluate(transcript, question)

    def answer_question(
        self,
        question: str,
        *,
        concept: Concept | None,
        sections: list[ResourceSection],
        history: list[tuple[str, str]] | None = None,
    ) -> GroundedAnswer:
        """Answer from the student's own material, and say when it cannot."""
        if not sections:
            subject = f" about {_spoken_name(concept.name)}" if concept else ""
            return GroundedAnswer(
                text=(
                    f"I could not find anything in your uploaded material{subject} that answers that. "
                    "I would rather tell you that than guess. If you add a resource that covers it, "
                    "ask me again and I will work from that."
                ),
                used_outside_knowledge=False,
            )

        best = sections[0]
        passage = ta.summarise(best.text, max_sentences=3)
        opener = "Here is what your material says on that."
        if concept:
            opener = f"Going back to {_spoken_name(concept.name)}, here is what your material says."
        closing = (
            " If that is not the angle you wanted, tell me what part is still unclear "
            "and I will come at it differently."
        )
        note = f"From {best.heading}" if best.heading else "From your uploaded material"
        return GroundedAnswer(
            text=f"{opener} {_sentence_case(_as_spoken(passage))}{closing}",
            source_section_ids=[s.id for s in sections[:3]],
            source_note=note,
        )

    def reexplain(
        self,
        concept: Concept,
        sections: list[ResourceSection],
        *,
        mode: str = "simpler",
    ) -> GroundedAnswer:
        name = _spoken_name(concept.name)
        source_ids = [s.id for s in sections[:3]] or concept.section_ids[:3]
        note = "From your uploaded material" if source_ids else ""

        if mode == "example":
            if concept.examples:
                text = (
                    f"Here is a case from your material. {_sentence_case(concept.examples[0])} "
                    "Try to see which part of it the idea is doing the work in."
                )
            else:
                text = (
                    f"Your material does not work through an example of {name}, so build one. "
                    f"Describe a situation you already understand, then point at where {name} shows up in it. "
                    "If you cannot find it, that gap is the useful thing to tell me about."
                )
        elif mode == "different":
            core = concept.summary or concept.definition or name
            text = (
                f"Let us come at {name} from another side. Instead of what it is, ask what it rules out. "
                f"{_sentence_case(_as_spoken(core))} Now ask yourself what could not happen if that were true. "
                "Thinking about what an idea forbids often lands better than what it permits."
            )
        else:  # simpler
            core = concept.definition or concept.summary
            plain = ta.summarise(core, max_sentences=1) if core else ""
            text = (
                f"Simply put: {_sentence_case(_as_spoken(plain))} "
                "Strip away the vocabulary and that is the whole of it. "
                "The terminology exists to make that sentence precise, not to make it complicated."
            ) if plain else (
                f"At its simplest, {name} is one of the ideas your material keeps coming back to. "
                "Tell me which word in it is the one causing trouble and I will start there."
            )

        return GroundedAnswer(text=text, source_section_ids=source_ids, source_note=note)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = " ".join(item.lower().split())[:80]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
