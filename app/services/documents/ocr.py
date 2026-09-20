"""Reading a photograph of notes.

Most revision material is a photo: a whiteboard, a page of a textbook, a
worked solution on paper. There is no text layer in a photo, so something has
to read the pixels.

Two readers, tried in order, and neither is guaranteed to be here:

    Tesseract   local, free, offline, good on printed text and passable on
                neat handwriting. Needs a binary installed on the machine.
    Claude      needs a key and a network, reads handwriting and diagrams far
                better, and costs money per image.

If neither is available the image is refused with a message that names both
fixes. That refusal matters more than the feature: a silent empty import
would leave the student with a subject that teaches nothing and no idea why.
"""

from __future__ import annotations

import base64
from pathlib import Path

from ...domain.models import ResourceKind
from .base import Block, ExtractedDocument, UnreadableDocument

EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")

#: Below this, whatever came back is noise rather than notes.
MIN_CHARACTERS = 12

_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}

_NOTHING_INSTALLED = (
    "This is a photo, so it needs to be read with OCR, and this machine has "
    "no way to do that yet. Either install Tesseract "
    "(winget install --id UB-Mannheim.TesseractOCR -e, then pip install "
    "pytesseract), or set ANTHROPIC_API_KEY in .env to read photos with "
    "Claude. Typing the notes out also works."
)

_UNREADABLE = (
    "No text could be found in this photo. A sharper picture, taken straight "
    "on with the writing filling the frame, usually fixes it."
)


def _read_with_tesseract(path: Path) -> str:
    """Local OCR. Returns "" when Tesseract is not installed."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        with Image.open(path) as image:
            return pytesseract.image_to_string(image)
    except Exception:
        # Covers a missing binary, an unreadable file and a corrupt image
        # alike. Any of them mean the same thing here: try the next reader.
        return ""


def _read_with_vision(path: Path) -> str:
    """Claude reads the image. Returns "" when there is no key."""
    from ..providers.anthropic import AnthropicProvider as AnthropicClient

    client = AnthropicClient.from_env()
    if client is None:
        return ""
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "image/png")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    try:
        return client.read_image(
            data,
            media_type=media_type,
            prompt=(
                "Transcribe every word in this photograph of study material, "
                "keeping headings on their own lines. Write formulas as they "
                "appear. Reply with the text only, nothing else. If the image "
                "has no readable writing in it, reply with nothing at all."
            ),
        )
    except Exception:
        return ""


def read_image(path: Path) -> str:
    """Text from a photograph, or a refusal that says what would fix it."""
    for reader in (_read_with_tesseract, _read_with_vision):
        text = (reader(path) or "").strip()
        if len(text) >= MIN_CHARACTERS:
            return text
        if text:
            # Something read it and found almost nothing. That is a picture
            # problem, not a missing-software problem, so say the useful thing.
            raise UnreadableDocument(_UNREADABLE)

    if _installed_readers():
        raise UnreadableDocument(_UNREADABLE)
    raise UnreadableDocument(_NOTHING_INSTALLED)


def _installed_readers() -> bool:
    """Whether any reader could have worked, so the message can tell the truth."""
    from ..providers.anthropic import AnthropicProvider as AnthropicClient

    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        return AnthropicClient.from_env() is not None


class ImageProcessor:
    kind = ResourceKind.IMAGE
    extensions = EXTENSIONS

    def can_handle(self, path: Path, mime_type: str = "") -> bool:
        return path.suffix.lower() in self.extensions or mime_type.startswith("image/")

    def extract(self, path: Path) -> ExtractedDocument:
        text = read_image(path)
        # OCR output is one block per line. Keep the lines: a whiteboard's
        # layout is most of its structure, and the chunker reads blank lines.
        blocks = [Block(text=line.strip()) for line in text.splitlines() if line.strip()]
        return ExtractedDocument(
            title=path.stem.replace("_", " ").replace("-", " ").strip(),
            blocks=blocks,
            page_count=1,
            kind=ResourceKind.IMAGE,
        )
