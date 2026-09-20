"""Finding the passages that answer a question.

Everything the teacher says about the subject should be traceable to something
the student uploaded. This is what makes that possible.

The default index is BM25 over stemmed terms. That is a deliberate choice, not
a placeholder: for a single subject's worth of material, on a machine with no
GPU and possibly no network, lexical retrieval is fast, needs no model, and
handles the queries students actually ask ("what does inertia mean", "explain
the second law") at least as well as a small embedding model would.

`HashingEmbeddings` implements `EmbeddingProvider` locally, so a hosted
embedding model can replace it behind the same interface.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from ..domain.assessment import stem
from ..domain.models import ResourceSection
from .textanalysis import content_words

# BM25 parameters. Standard values; k1 controls term-frequency saturation and
# b controls how much passage length is penalised.
K1 = 1.4
B = 0.72


@dataclass
class Hit:
    section: ResourceSection
    score: float


@dataclass
class SpaceIndex:
    """An inverted index over one learning space's passages."""

    postings: dict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(dict))
    lengths: dict[str, int] = field(default_factory=dict)
    sections: dict[str, ResourceSection] = field(default_factory=dict)
    average_length: float = 0.0

    @property
    def size(self) -> int:
        return len(self.sections)


def build_index(sections: list[ResourceSection]) -> SpaceIndex:
    index = SpaceIndex()
    for section in sections:
        tokens = [stem(w) for w in content_words(f"{section.heading} {section.heading} {section.text}")]
        if not tokens:
            continue
        index.sections[section.id] = section
        index.lengths[section.id] = len(tokens)
        for term, count in Counter(tokens).items():
            index.postings[term][section.id] = count
    if index.lengths:
        index.average_length = sum(index.lengths.values()) / len(index.lengths)
    return index


def search(index: SpaceIndex, query: str, *, limit: int = 4) -> list[Hit]:
    """Top passages for a query, by BM25."""
    if not index.sections:
        return []
    terms = [stem(w) for w in content_words(query)]
    if not terms:
        return []

    total = len(index.sections)
    scores: dict[str, float] = defaultdict(float)

    for term in terms:
        postings = index.postings.get(term)
        if not postings:
            continue
        # Probabilistic IDF, floored so a term in every passage still counts.
        document_frequency = len(postings)
        idf = max(0.05, math.log(1 + (total - document_frequency + 0.5) / (document_frequency + 0.5)))
        for section_id, frequency in postings.items():
            length = index.lengths[section_id]
            norm = 1 - B + B * (length / index.average_length if index.average_length else 1)
            scores[section_id] += idf * (frequency * (K1 + 1)) / (frequency + K1 * norm)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
    return [Hit(index.sections[sid], round(score, 4)) for sid, score in ranked if sid in index.sections]


class RetrievalService:
    """Per-space retrieval with a small cache.

    Indexes are rebuilt when a space's material changes, which the resource
    pipeline signals by calling `invalidate`.
    """

    def __init__(self) -> None:
        self._indexes: dict[str, SpaceIndex] = {}

    def index_space(self, space_id: str, sections: list[ResourceSection]) -> SpaceIndex:
        index = build_index(sections)
        self._indexes[space_id] = index
        return index

    def invalidate(self, space_id: str) -> None:
        self._indexes.pop(space_id, None)

    def is_indexed(self, space_id: str) -> bool:
        return space_id in self._indexes

    def search(
        self,
        space_id: str,
        query: str,
        *,
        limit: int = 4,
        sections: list[ResourceSection] | None = None,
    ) -> list[ResourceSection]:
        index = self._indexes.get(space_id)
        if index is None:
            if sections is None:
                return []
            index = self.index_space(space_id, sections)
        return [hit.section for hit in search(index, query, limit=limit)]


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbeddings:
    """Local embeddings by feature hashing, implementing `EmbeddingProvider`.

    No model and no network: terms are hashed into a fixed number of buckets
    and weighted by sub-linear term frequency, then L2-normalised so cosine
    similarity is a plain dot product.

    Good enough to cluster related passages. A hosted embedding model will beat
    it on paraphrase, and can be swapped in behind the same interface.
    """

    name = "hashing"
    dimensions = 512

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self.dimensions
            counts = Counter(stem(w) for w in _WORD.findall(text.lower()))
            for term, count in counts.items():
                bucket = hash(term) % self.dimensions
                vector[bucket] += 1.0 + math.log(count)
            norm = math.sqrt(sum(v * v for v in vector))
            vectors.append([v / norm for v in vector] if norm else vector)
        return vectors
