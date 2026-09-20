"""Text analysis used to understand a student's material without a model.

This is what lets the product do real work with no API key: it reads the actual
uploaded document and pulls out the things a teacher would notice first, which
are headings, definitions, formulas, repeated technical terms and worked
examples.

It is honest about what it is. This finds structure that is present in the
text. It does not invent knowledge. Everything downstream is grounded in
passages that can be pointed at.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from ..domain.assessment import stem

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    """a about above after again against all also am an and any are as at
    be because been before being below between both but by can cannot could
    did do does doing down during each few for from further had has have having
    he her here hers herself him himself his how i if in into is it its itself
    just me more most my myself no nor not now of off on once only or other our
    ours out over own same she should so some such than that the their theirs
    them themselves then there these they this those through to too under until
    up very was we were what when where which while who whom why will with you
    your yours yourself yourselves would shall may might must been being
    figure table chapter section page example note remember summary exercise
    following above below shown given let us using used use
    next another many much often always never usually
    called known means meaning way ways thing things part parts type types
    like such e.g i.e etc""".split()
)

# Ordinals are deliberately NOT stopwords. "First" is the whole difference
# between Newton's First Law and his Second, and dropping it collapses three
# distinct concepts into one.

# Words that mark a sentence as defining something.
_DEFINITION_CUES = (
    " is defined as ", " is the ", " is a ", " is an ", " are the ", " are a ",
    " refers to ", " means that ", " means ", " is called ", " are called ",
    " can be described as ", " is known as ", " are known as ", " is simply ",
    " we call ", " is when ", " occurs when ", " happens when ",
)

_EXAMPLE_CUES = (
    "for example", "for instance", "suppose", "imagine", "consider a",
    "consider the case", "worked example", "e.g.", "say you", "think of",
    "picture a", "take the case",
)

_MISCONCEPTION_CUES = (
    "common mistake", "common misconception", "students often", "a common error",
    "it is tempting to", "do not confuse", "don't confuse", "be careful not to",
    "contrary to", "this does not mean", "it is not true that", "many people think",
    "a frequent error", "often confused",
)

_IMPORTANCE_CUES = (
    "important", "key", "note that", "remember", "crucially", "fundamental",
    "essential", "the main", "principle", "law of", "rule",
)

# A formula: a short symbol, an equals sign, then a short symbolic expression
# ending at punctuation or at the "where ..." clause that usually follows it.
# Anchoring the end this way is what lets "The law is written as F = m a, where
# F is the resultant force..." yield "F = m a" out of a long sentence.
_FORMULA = re.compile(
    r"^[^.!?]{0,80}?[A-Za-z_][A-Za-z0-9_]{0,12}\s*=\s*[^=]{1,60}$"
)
_FORMULA_INLINE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]{0,4}\s*=\s*"
    r"[A-Za-z0-9π∆Δ][A-Za-z0-9_π∆Δ\s\*/\+\-\^\(\)\.·×÷]{0,28}?)"
    # A full stop only ends the formula when it ends the sentence too,
    # otherwise "E = 0.5 m v^2" is truncated to "E = 0" at the decimal point.
    # The word list catches the prose that typically resumes after a formula,
    # as in "d = m / v for any material".
    r"(?=\s*(?:[,;:]|\.(?:\s|$)|$|"
    r"\b(?:where|when|in which|for|and|so|with|to|at|of|which|giving|gives|"
    r"therefore|hence|thus|since|because|if|then|this|that|is|are|was|were)\b))",
    re.I,
)

# A phrase ending in one of these is a clause, not the name of a thing, so it
# must not be taken as the subject of a definition.
_NOT_A_SUBJECT_TAIL = frozenset(
    """describes relates states shows tells explains means defines says gives
    what that which happens occurs depends works acts""".split()
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])|\n{2,}")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-']*")

# A heading looks like a heading: short, title-ish, no terminal full stop.
_NUMBERED_HEADING = re.compile(r"^\s*(\d+(\.\d+)*|[IVXLC]+\.|[A-Z]\.)\s+(?P<t>.{2,70})$")


def sentences(text: str) -> list[str]:
    """Split into sentences, tolerant of the ragged text PDFs produce."""
    parts: list[str] = []
    for block in _SENTENCE_SPLIT.split(text):
        block = " ".join(block.split())
        if block:
            parts.append(block)
    return parts


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def content_words(text: str) -> list[str]:
    return [w.lower() for w in words(text) if w.lower() not in _STOPWORDS and len(w) > 2]


def is_heading(line: str) -> bool:
    """True when a line reads as a heading rather than prose."""
    stripped = line.strip()
    if not (2 <= len(stripped) <= 80):
        return False
    if stripped.endswith((".", ",", ";", ":")) and not _NUMBERED_HEADING.match(stripped):
        return False
    token_count = len(stripped.split())
    if token_count > 12:
        return False
    if _NUMBERED_HEADING.match(stripped):
        return True
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return False
    # ALL CAPS, or Title Case with most words capitalised.
    if all(c.isupper() for c in letters) and token_count <= 10:
        return True
    capitalised = sum(1 for w in stripped.split() if w[:1].isupper())
    return token_count <= 8 and capitalised >= max(1, int(token_count * 0.6))


def clean_heading(line: str) -> str:
    match = _NUMBERED_HEADING.match(line.strip())
    text = match.group("t") if match else line.strip()
    text = text.strip(" .:-–—")
    if text.isupper() and len(text) > 3:
        text = text.title()
    return text


# ---------------------------------------------------------------------------
# Term extraction
# ---------------------------------------------------------------------------


@dataclass
class Term:
    """A candidate technical term, with why it was picked."""

    text: str
    count: int = 0
    document_frequency: int = 0    # how many sections mention it
    score: float = 0.0
    in_heading: bool = False
    defined: bool = False

    @property
    def key(self) -> str:
        return " ".join(stem(w) for w in self.text.lower().split())


def _ngrams(tokens: list[str], n: int) -> list[tuple[str, ...]]:
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def extract_terms(
    passages: list[str], *, headings: list[str] | None = None, limit: int = 60
) -> list[Term]:
    """Find the terms this material is actually about.

    Scores unigrams and bigrams by frequency, spread across the document, and
    whether they appear in headings. Terms that appear everywhere (a textbook
    saying "energy" on every page) and terms that appear once both score low;
    the useful ones recur in several places without being ubiquitous.
    """
    headings = headings or []
    heading_terms = {w for h in headings for w in content_words(h)}

    total_documents = max(1, len(passages))
    counts: Counter[str] = Counter()
    document_counts: Counter[str] = Counter()
    display: dict[str, str] = {}

    for passage in passages:
        tokens = content_words(passage)
        seen: set[str] = set()
        candidates: list[tuple[str, str]] = [(t, t) for t in tokens]
        for gram in _ngrams(tokens, 2):
            phrase = " ".join(gram)
            candidates.append((phrase, phrase))
        for key_source, shown in candidates:
            key = " ".join(stem(w) for w in key_source.split())
            counts[key] += 1
            display.setdefault(key, shown)
            if key not in seen:
                document_counts[key] += 1
                seen.add(key)

    terms: list[Term] = []
    for key, count in counts.items():
        if count < 2 and " " not in key:
            continue
        document_frequency = document_counts[key]
        # Inverse document frequency, softened so common-but-real terms survive.
        idf = math.log(1 + total_documents / document_frequency)
        spread = min(1.0, document_frequency / max(2.0, total_documents * 0.5))
        score = count * (0.5 + idf) * (0.6 + spread)
        if " " in key:
            score *= 1.6          # multi-word terms are usually the real ones
        shown = display[key]
        if any(w in heading_terms for w in shown.split()):
            score *= 2.2
        terms.append(
            Term(
                text=shown,
                count=count,
                document_frequency=document_frequency,
                score=score,
                in_heading=any(w in heading_terms for w in shown.split()),
            )
        )

    terms.sort(key=lambda t: (-t.score, t.text))
    return _drop_subsumed(terms)[:limit]


def _drop_subsumed(terms: list[Term]) -> list[Term]:
    """Remove unigrams already covered by a stronger bigram, and duplicates."""
    kept: list[Term] = []
    covered: set[str] = set()
    for term in terms:
        key = term.key
        if key in covered:
            continue
        if " " not in key:
            # Skip a single word if a higher-scoring phrase already contains it.
            if any(key in k.split() for k in covered if " " in k):
                continue
        covered.add(key)
        kept.append(term)
    return kept


# ---------------------------------------------------------------------------
# Sentence classification
# ---------------------------------------------------------------------------


@dataclass
class Findings:
    """Everything worth teaching that was found in a passage."""

    definitions: list[tuple[str, str]] = field(default_factory=list)  # (term, sentence)
    facts: list[str] = field(default_factory=list)
    formulas: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    misconceptions: list[str] = field(default_factory=list)


# Long words that legitimately appear inside a formula. Anything else long on
# the right of the equals sign means the "formula" is really a sentence.
_SYMBOL_WORDS = frozenset(
    """lambda alpha beta gamma delta theta omega sigma rho mu pi tau phi psi
    epsilon kappa nu eta zeta chi
    sin cos tan log ln exp sqrt abs max min sum
    mass force area time work heat rate""".split()
)


def _is_formula(candidate: str) -> bool:
    """True when this reads as a relationship rather than a recorded value.

    "F = m a" is worth teaching. "x = 5 was recorded" is a sentence that
    happens to contain an equals sign, and treating it as a formula puts
    nonsense into a lesson.
    """
    if "=" not in candidate:
        return False
    right = candidate.split("=", 1)[1].strip()
    if not right:
        return False

    words = _WORD.findall(right)
    if not words:
        return False

    # Prose gives itself away with a long ordinary word on the right.
    for word in words:
        if len(word) >= 5 and word.lower() not in _SYMBOL_WORDS:
            return False

    has_operator = any(op in right for op in "+-*/^·×÷()")
    return has_operator or len(words) > 1


def _subject_of(sentence: str) -> str:
    """The phrase before the defining verb, which is the term being defined."""
    lowered = sentence.lower()
    for cue in _DEFINITION_CUES:
        index = lowered.find(cue)
        if index > 0:
            subject = sentence[:index].strip(" ,;:")
            subject = re.sub(r"^(the|a|an|this|these|that|those|in|when)\s+", "", subject, flags=re.I)
            parts = subject.split()
            if not (1 <= len(parts) <= 5) or not subject[:1].isalpha():
                continue
            if parts[-1].lower().strip(",") in _NOT_A_SUBJECT_TAIL:
                continue
            return subject
    return ""


def analyse_passage(text: str) -> Findings:
    """Pull definitions, facts, formulas, examples and warnings from a passage."""
    findings = Findings()
    for raw in sentences(text):
        sentence = raw.strip()
        if len(sentence) < 12:
            continue
        lowered = sentence.lower()

        formula_match = _FORMULA_INLINE.search(sentence)
        if _FORMULA.match(sentence) or formula_match:
            candidate = (formula_match.group(1) if formula_match else sentence).strip()
            if _is_formula(candidate) and candidate not in findings.formulas:
                findings.formulas.append(candidate)
            if _FORMULA.match(sentence):
                continue

        if any(cue in lowered for cue in _MISCONCEPTION_CUES):
            findings.misconceptions.append(sentence)
            continue

        if any(cue in lowered for cue in _EXAMPLE_CUES):
            findings.examples.append(sentence)
            continue

        subject = _subject_of(sentence)
        if subject and len(sentence.split()) <= 45:
            findings.definitions.append((subject, sentence))
            continue

        if any(cue in lowered for cue in _IMPORTANCE_CUES) or 8 <= len(sentence.split()) <= 40:
            findings.facts.append(sentence)

    return findings


# Language that signals a sentence carries the substance of an idea: a
# relationship, a cause, a quantity, a consequence.
_SUBSTANTIVE_CUES = (
    "proportional", "increases", "decreases", "greater", "smaller", "larger",
    "equal", "opposite", "depends", "because", "causes", "results in", "leads to",
    "the same", "doubling", "halving", "always", "never", "cannot", "must",
    "converts", "measured in", "calculated", "unless", "provided",
)

# Language that describes an idea from outside rather than stating it. These
# sentences read as filler when they become the expected answer to a question.
_META_OPENERS = (
    "describes what", "is about", "relates the", "states that", "tells us",
    "in this section", "we will", "this chapter", "it is important to remember",
    "as we saw", "recall that",
)


def informativeness(sentence: str) -> float:
    """How much substance a sentence carries, for ranking facts.

    Used to choose what a question actually asks about. A sentence saying what
    a law *is about* makes a poor expected answer; one stating the relationship
    it asserts makes a good one.
    """
    lowered = sentence.lower()
    score = 0.0
    score += sum(1.5 for cue in _SUBSTANTIVE_CUES if cue in lowered)
    score -= sum(2.5 for cue in _META_OPENERS if cue in lowered)
    if re.search(r"\d", sentence):
        score += 1.0
    if "=" in sentence:
        score += 1.5
    word_count = len(sentence.split())
    if word_count < 6:
        score -= 1.5
    elif word_count > 40:
        score -= 1.0
    density = len(content_words(sentence)) / max(1, word_count)
    score += density * 2
    return score


def rank_by_informativeness(sentences_in: list[str]) -> list[str]:
    """Most substantive first, ties broken by original order."""
    indexed = list(enumerate(sentences_in))
    indexed.sort(key=lambda pair: (-informativeness(pair[1]), pair[0]))
    return [sentence for _index, sentence in indexed]


def readability_difficulty(text: str) -> int:
    """A 1-4 difficulty estimate from sentence length and word length.

    Used to pace a lesson, never shown to the student as a rating.
    """
    sentence_list = sentences(text) or [text]
    word_list = words(text) or ["x"]
    average_sentence = len(word_list) / max(1, len(sentence_list))
    long_words = sum(1 for w in word_list if len(w) >= 9) / len(word_list)
    score = average_sentence / 12.0 + long_words * 4.0
    if score < 1.4:
        return 1
    if score < 2.0:
        return 2
    if score < 2.7:
        return 3
    return 4


def summarise(text: str, *, max_sentences: int = 2) -> str:
    """Pick the most representative sentences from a passage.

    Classic centroid scoring: sentences made of the passage's frequent content
    words score highest. Extractive, so it cannot state anything the source
    does not.
    """
    sentence_list = [s for s in sentences(text) if len(s.split()) >= 6]
    if not sentence_list:
        return " ".join(text.split())[:220]
    frequency = Counter(stem(w) for w in content_words(text))
    if not frequency:
        return sentence_list[0]
    top = max(frequency.values())

    def score(sentence: str) -> float:
        tokens = [stem(w) for w in content_words(sentence)]
        if not tokens:
            return 0.0
        return sum(frequency[t] / top for t in tokens) / math.sqrt(len(tokens))

    ranked = sorted(sentence_list, key=score, reverse=True)[:max_sentences]
    ranked.sort(key=sentence_list.index)
    return " ".join(ranked)
