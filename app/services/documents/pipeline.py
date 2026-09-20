"""The ingestion pipeline.

    UPLOAD -> VALIDATE -> EXTRACT -> CLEAN -> CHUNK -> STRUCTURE

What comes out is a list of `ResourceSection`s: cleaned, heading-aware passages
sized for both retrieval and teaching. The knowledge model is built from these
by a `TeachingEngine`, and every lesson can point back at the sections it came
from.

Validation is not a formality. Uploaded files are checked by signature rather
than by the name the browser supplied, stored under a generated name so a
crafted filename cannot escape the upload directory, and capped in size.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ...domain.models import ResourceKind, ResourceSection, new_id
from .. import textanalysis as ta
from .base import Block, ExtractedDocument, UnreadableDocument, UnsupportedDocument
from .media import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS, MediaProcessor
from .ocr import EXTENSIONS as IMAGE_EXTENSIONS
from .ocr import ImageProcessor
from .parsers import PROCESSORS, HtmlProcessor

#: Passage sizing. Small enough to retrieve precisely, large enough to teach from.
TARGET_WORDS = 240
MAX_WORDS = 420
MIN_WORDS = 40

#: Leading bytes that identify a format, checked against the claimed extension.
_SIGNATURES: tuple[tuple[bytes, frozenset[str]], ...] = (
    (b"%PDF-", frozenset({".pdf"})),
    (b"PK\x03\x04", frozenset({".docx", ".pptx"})),   # any OOXML is a zip
    (b"\x1a\x45\xdf\xa3", frozenset({".webm", ".mkv"})),   # Matroska
    (b"OggS", frozenset({".ogg", ".oga", ".opus"})),
    (b"fLaC", frozenset({".flac"})),
    (b"\x89PNG\r\n\x1a\n", frozenset({".png"})),
    (b"\xff\xd8\xff", frozenset({".jpg", ".jpeg"})),
    (b"GIF8", frozenset({".gif"})),
    (b"BM", frozenset({".bmp"})),
)

#: Signatures that do not begin at byte zero.
_SIGNATURES_AT: tuple[tuple[int, bytes, frozenset[str]], ...] = (
    # ISO base media: a four-byte size, then "ftyp". Covers mp4, mov, m4a, m4v.
    (4, b"ftyp", frozenset({".mp4", ".mov", ".m4a", ".m4v"})),
    # RIFF....WEBP
    (8, b"WEBP", frozenset({".webp"})),
)

_DOCUMENT_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".text", ".rst", ".html", ".htm"}
)
_ALLOWED_EXTENSIONS = (
    _DOCUMENT_EXTENSIONS | frozenset(MEDIA_EXTENSIONS) | frozenset(IMAGE_EXTENSIONS)
)


def is_media(extension: str) -> bool:
    """True when this file needs transcribing rather than parsing."""
    return extension.lower() in MEDIA_EXTENSIONS

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9 ._-]")


class UploadRejected(ValueError):
    """The upload failed validation. The message is safe to show a student."""


def safe_display_name(filename: str) -> str:
    """A filename fit to show on screen. Never used as a path."""
    name = unicodedata.normalize("NFKD", filename or "").strip()
    name = _FILENAME_SAFE.sub("", name).strip(" .")
    return (name or "Untitled")[:120]


def size_label(max_bytes: int) -> str:
    megabytes = max_bytes / (1024 * 1024)
    return f"{megabytes:.0f} MB" if megabytes >= 1 else f"{max_bytes // 1024} KB"


def check_name(filename: str) -> str:
    """Validate the extension alone, before any bytes have been read."""
    extension = Path(safe_display_name(filename)).suffix.lower()
    if extension not in _ALLOWED_EXTENSIONS:
        raise UploadRejected(
            f"{extension or 'That file type'} is not supported. You can add PDF, Word, "
            "PowerPoint, text and Markdown documents, plus video and audio recordings."
        )
    return extension


def check_signature(extension: str, head: bytes) -> None:
    """Check the leading bytes against the claimed extension.

    The browser's filename is a claim; the bytes are the evidence. Formats with
    no single reliable magic number are not asserted on: plain text has none,
    and an MP3 may or may not carry an ID3 header, so rejecting on a missing
    signature there would throw out real files.
    """
    if not head:
        raise UploadRejected("That file is empty.")

    for signature, extensions in _SIGNATURES:
        if head.startswith(signature) and extension not in extensions:
            raise UploadRejected("That file's contents do not match its name.")
        if extension in extensions and not head.startswith(signature):
            raise UploadRejected(
                f"This does not look like a valid {extension} file. It may be damaged "
                "or renamed from another format."
            )

    for offset, signature, extensions in _SIGNATURES_AT:
        present = head[offset : offset + len(signature)] == signature
        if present and extension not in extensions:
            raise UploadRejected("That file's contents do not match its name.")
        if extension in extensions and not present:
            raise UploadRejected(
                f"This does not look like a valid {extension} file. It may be damaged "
                "or renamed from another format."
            )


def kind_for(extension: str) -> ResourceKind:
    if extension in IMAGE_EXTENSIONS:
        return ResourceKind.IMAGE
    if extension in VIDEO_EXTENSIONS:
        return ResourceKind.VIDEO
    if extension in AUDIO_EXTENSIONS:
        return ResourceKind.AUDIO
    return {
        ".pdf": ResourceKind.PDF,
        ".docx": ResourceKind.DOCX,
        ".pptx": ResourceKind.PPTX,
        ".md": ResourceKind.MARKDOWN,
        ".markdown": ResourceKind.MARKDOWN,
        ".html": ResourceKind.URL,
        ".htm": ResourceKind.URL,
    }.get(extension, ResourceKind.TEXT)


def validate_upload(filename: str, data: bytes, *, max_bytes: int) -> tuple[str, ResourceKind]:
    """Check a whole in-memory upload. Used for documents and by tests.

    Recordings go through `check_name` plus `store_stream` instead, so a large
    file never has to exist in memory to be measured.
    """
    if not data:
        raise UploadRejected("That file is empty.")
    if len(data) > max_bytes:
        raise UploadRejected(
            f"That file is larger than {size_label(max_bytes)}. Try splitting it up."
        )
    extension = check_name(filename)
    check_signature(extension, data[:64])
    return extension, kind_for(extension)


def store_stream(
    chunks, *, resource_id: str, extension: str, directory: Path, max_bytes: int
) -> tuple[Path, int]:
    """Write an upload to disk as it arrives, enforcing the cap as it goes.

    A lecture recording can run to hundreds of megabytes. Buffering one in
    memory to measure it would let a single upload decide how much RAM the
    server uses, so the limit is checked against the running total and a file
    that overruns is deleted rather than left behind.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / f"{resource_id}{extension}").resolve()
    if directory.resolve() not in path.parents:
        raise UploadRejected("That file could not be stored.")

    total = 0
    try:
        with path.open("wb") as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise UploadRejected(
                        f"That file is larger than {size_label(max_bytes)}. "
                        "Try a shorter recording, or split it up."
                    )
                handle.write(chunk)
    except UploadRejected:
        path.unlink(missing_ok=True)
        raise
    except OSError:
        path.unlink(missing_ok=True)
        raise UploadRejected("That file could not be stored.") from None

    if total == 0:
        path.unlink(missing_ok=True)
        raise UploadRejected("That file is empty.")
    return path, total


def store_upload(data: bytes, *, resource_id: str, extension: str, directory: Path) -> Path:
    """Write an upload under a generated name inside `directory`.

    The client's filename never reaches the filesystem, which removes path
    traversal as a category rather than trying to sanitise it away.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / f"{resource_id}{extension}").resolve()
    if directory.resolve() not in path.parents:
        raise UploadRejected("That file could not be stored.")
    path.write_bytes(data)
    return path


def extract(path: Path, *, mime_type: str = "", stt=None) -> ExtractedDocument:
    """Run the first reader that recognises this file.

    Recordings are checked first, and need a speech provider, which is why this
    takes one rather than relying only on the stateless readers below.
    """
    media = MediaProcessor(stt)
    if media.can_handle(path, mime_type):
        return media.extract(path)

    photo = ImageProcessor()
    if photo.can_handle(path, mime_type):
        return photo.extract(path)

    for processor in PROCESSORS:
        if processor.can_handle(path, mime_type):    # type: ignore[attr-defined]
            return processor.extract(path)           # type: ignore[attr-defined]
    raise UnsupportedDocument(f"No reader is available for {path.suffix or 'that file'}.")


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REPEATED_PUNCT = re.compile(r"([.\-_=*~])\1{3,}")
_PAGE_NUMBER = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.I)


def clean_blocks(blocks: list[Block]) -> list[Block]:
    """Strip the artefacts of extraction: headers, page numbers, broken words."""
    # Lines that repeat on many pages are running headers or footers.
    counts: dict[str, int] = {}
    for block in blocks:
        key = block.text.strip().lower()
        if len(key) < 80:
            counts[key] = counts.get(key, 0) + 1
    pages = max((b.page for b in blocks), default=0)
    repeat_threshold = max(3, pages // 3) if pages else 10**9

    cleaned: list[Block] = []
    for block in blocks:
        text = _CONTROL.sub("", block.text)
        text = _HYPHEN_BREAK.sub(r"\1\2", text)
        text = _REPEATED_PUNCT.sub(r"\1", text)
        text = " ".join(text.split())
        if not text or _PAGE_NUMBER.match(text):
            continue
        if counts.get(text.lower(), 0) >= repeat_threshold and len(text) < 80:
            continue
        if len(text) < 3:
            continue
        cleaned.append(Block(text, page=block.page, is_heading=block.is_heading))
    return cleaned


def _join(lines: list[str]) -> str:
    """Rejoin extracted lines into prose.

    PDF extraction breaks sentences at every line, so lines that do not end a
    sentence are joined to the next rather than left as fragments.
    """
    if not lines:
        return ""
    out = lines[0]
    for line in lines[1:]:
        if out.endswith(("-",)):
            out = out[:-1] + line
        elif out.endswith((".", "!", "?", ":", ";")) or line[:1].isupper() and len(out) > 120:
            out += " " + line
        else:
            out += " " + line
    return " ".join(out.split())


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk(blocks: list[Block]) -> list[tuple[str, str, int]]:
    """Group blocks into (heading, text, page) passages.

    Headings own the text beneath them. A run that grows past `MAX_WORDS` is
    split at a sentence boundary, keeping its heading, so a long chapter
    becomes several passages about the same topic rather than one unusable one.
    """
    groups: list[tuple[str, list[str], int]] = []
    heading = ""
    buffer: list[str] = []
    page = 0

    def flush() -> None:
        nonlocal buffer
        if buffer:
            groups.append((heading, buffer, page))
            buffer = []

    for block in blocks:
        if block.is_heading:
            flush()
            heading = block.text
            page = block.page
            continue
        if not buffer:
            page = block.page
        buffer.append(block.text)
    flush()

    passages: list[tuple[str, str, int]] = []
    for group_heading, lines, group_page in groups:
        text = _join(lines)
        words = text.split()
        if len(words) <= MAX_WORDS:
            passages.append((group_heading, text, group_page))
            continue
        for piece in _split_long(text):
            passages.append((group_heading, piece, group_page))

    # Fold a passage too small to teach from into its neighbour.
    merged: list[tuple[str, str, int]] = []
    for group_heading, text, group_page in passages:
        if merged and len(text.split()) < MIN_WORDS and merged[-1][0] == group_heading:
            previous = merged.pop()
            merged.append((previous[0], f"{previous[1]} {text}".strip(), previous[2]))
        else:
            merged.append((group_heading, text, group_page))
    return merged


def _split_long(text: str) -> list[str]:
    """Break a long passage at sentence boundaries near the target size."""
    pieces: list[str] = []
    current: list[str] = []
    count = 0
    for sentence in ta.sentences(text):
        length = len(sentence.split())
        if count + length > TARGET_WORDS and current:
            pieces.append(" ".join(current))
            current, count = [], 0
        current.append(sentence)
        count += length
    if current:
        tail = " ".join(current)
        # Do not leave a scrap at the end; fold it back into the previous piece.
        if pieces and len(tail.split()) < MIN_WORDS:
            pieces[-1] = f"{pieces[-1]} {tail}"
        else:
            pieces.append(tail)
    return pieces or [text]


def to_sections(
    document: ExtractedDocument, *, resource_id: str, space_id: str
) -> list[ResourceSection]:
    """The full pipeline from an extracted document to retrievable passages."""
    cleaned = clean_blocks(document.blocks)
    passages = chunk(cleaned)

    sections: list[ResourceSection] = []
    for ordinal, (heading, text, page) in enumerate(passages):
        words = len(text.split())
        if words < 12:
            continue
        sections.append(
            ResourceSection(
                id=new_id("sec"),
                resource_id=resource_id,
                space_id=space_id,
                ordinal=ordinal,
                heading=ta.clean_heading(heading) if heading else "",
                text=text,
                page=page,
                word_count=words,
            )
        )
    return sections


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

_MAX_FETCH_BYTES = 5 * 1024 * 1024
_FETCH_TIMEOUT = 20


def _is_public_address(hostname: str) -> bool:
    """Reject anything that resolves to a private or local address.

    Without this, a URL field is a request forgery primitive pointed at
    whatever else runs on this machine or network.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return False
    return True


def fetch_url(url: str) -> ExtractedDocument:
    """Fetch a web page and extract its readable text."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UploadRejected("Only http and https links can be added.")
    if not parsed.hostname:
        raise UploadRejected("That does not look like a complete web address.")
    if not _is_public_address(parsed.hostname):
        raise UploadRejected("That address cannot be reached from here.")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Aloud/1.0 (learning resource importer)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as response:
            content_type = response.headers.get("content-type", "")
            if not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml")):
                raise UploadRejected(
                    "That link is not a web page. Download the file and upload it instead."
                )
            raw = response.read(_MAX_FETCH_BYTES + 1)
    except UploadRejected:
        raise
    except urllib.error.HTTPError as error:
        raise UploadRejected(f"That page could not be loaded ({error.code}).") from None
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        raise UploadRejected("That page could not be reached.") from None

    if len(raw) > _MAX_FETCH_BYTES:
        raise UploadRejected("That page is too large to import.")

    charset = "utf-8"
    if "charset=" in content_type:
        charset = content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")

    if "text/plain" in content_type:
        blocks = [
            Block(line.strip(), is_heading=ta.is_heading(line.strip()))
            for line in text.splitlines()
            if line.strip()
        ]
        document = ExtractedDocument(
            title=parsed.netloc, blocks=blocks, kind=ResourceKind.URL
        )
        if document.is_empty:
            raise UnreadableDocument("There is not enough text at that address to teach from.")
        return document

    return HtmlProcessor.from_html(text, title=parsed.netloc)
