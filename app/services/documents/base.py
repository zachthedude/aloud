"""Document processing interfaces.

Each processor turns one file format into the same shape: an ordered list of
blocks, each knowing whether it is a heading and which page it came from. The
pipeline downstream never needs to know which format it started as.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ...domain.models import ResourceKind


class UnsupportedDocument(ValueError):
    """The file is not a format this build can read."""


class UnreadableDocument(ValueError):
    """The format is supported but this particular file could not be read.

    Raised for encrypted PDFs, corrupt archives and scanned pages with no text
    layer. The message is written to be shown to a student.
    """


@dataclass
class Block:
    """One run of text from a document."""

    text: str
    page: int = 0
    is_heading: bool = False


@dataclass
class ExtractedDocument:
    title: str = ""
    blocks: list[Block] = field(default_factory=list)
    page_count: int = 0
    kind: ResourceKind = ResourceKind.TEXT

    @property
    def word_count(self) -> int:
        return sum(len(block.text.split()) for block in self.blocks)

    @property
    def is_empty(self) -> bool:
        return self.word_count < 20


@runtime_checkable
class DocumentProcessor(Protocol):
    """Reads one family of formats."""

    kind: ResourceKind
    extensions: tuple[str, ...]

    def can_handle(self, path: Path, mime_type: str = "") -> bool:
        ...

    def extract(self, path: Path) -> ExtractedDocument:
        ...
