"""Photographs of notes.

Most revision material is a photo of a whiteboard or a page, taken on a phone.
Reading it needs OCR, which needs something this machine may not have. The
point of these tests is the honesty of the failure: when Aloud cannot read an
image it says so and says what would fix it, rather than importing an empty
document and teaching nothing from it.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app.services.documents import ocr, pipeline
from app.services.documents.base import UnreadableDocument
from app.domain.models import ResourceKind


def a_png(text: str = "Newton's second law", size=(420, 120)) -> bytes:
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).text((12, 48), text, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class TestImagesAreAcceptedAtAll:
    def test_a_png_is_a_known_kind(self):
        assert pipeline.kind_for(".png") is ResourceKind.IMAGE

    def test_a_photo_passes_the_name_check(self):
        assert pipeline.check_name("whiteboard.jpg") == ".jpg"

    def test_a_png_must_actually_be_a_png(self):
        with pytest.raises(pipeline.UploadRejected):
            pipeline.check_signature(".png", b"not an image at all")

    def test_a_real_png_passes(self):
        pipeline.check_signature(".png", a_png()[:64])

    def test_a_jpeg_renamed_to_png_is_refused(self):
        jpeg_head = b"\xff\xd8\xff\xe0" + b"\x00" * 60
        with pytest.raises(pipeline.UploadRejected):
            pipeline.check_signature(".png", jpeg_head)


class TestReadingAnImage:
    def test_a_reader_that_works_produces_text(self, monkeypatch):
        monkeypatch.setattr(ocr, "_read_with_tesseract", lambda path: "Force equals mass times acceleration")
        assert "Force equals mass" in ocr.read_image(pipeline.Path("nope.png"))

    def test_whitespace_only_output_counts_as_unreadable(self, monkeypatch):
        monkeypatch.setattr(ocr, "_read_with_tesseract", lambda path: "   \n  \n ")
        with pytest.raises(UnreadableDocument):
            ocr.read_image(pipeline.Path("blank.png"))

    def test_with_nothing_installed_it_says_what_would_fix_it(self, monkeypatch):
        monkeypatch.setattr(ocr, "_read_with_tesseract", lambda path: "")
        monkeypatch.setattr(ocr, "_read_with_vision", lambda path: "")
        with pytest.raises(UnreadableDocument) as error:
            ocr.read_image(pipeline.Path("photo.png"))
        message = str(error.value)
        assert "tesseract" in message.lower()
        assert "ANTHROPIC_API_KEY" in message

    def test_vision_is_the_fallback_when_tesseract_is_absent(self, monkeypatch):
        monkeypatch.setattr(ocr, "_read_with_tesseract", lambda path: "")
        monkeypatch.setattr(ocr, "_read_with_vision", lambda path: "Momentum is mass times velocity")
        assert "Momentum" in ocr.read_image(pipeline.Path("photo.png"))


class TestAnImageBecomesADocument:
    def test_the_image_processor_claims_photos(self, tmp_path):
        path = tmp_path / "board.png"
        path.write_bytes(a_png())
        assert ocr.ImageProcessor().can_handle(path, "")

    def test_it_does_not_claim_a_pdf(self, tmp_path):
        path = tmp_path / "notes.pdf"
        path.write_bytes(b"%PDF-1.4")
        assert not ocr.ImageProcessor().can_handle(path, "")

    def test_extract_routes_a_photo_through_ocr(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            ocr, "_read_with_tesseract",
            lambda path: "A resultant force changes the motion of an object.",
        )
        path = tmp_path / "board.png"
        path.write_bytes(a_png())
        document = pipeline.extract(path)
        assert "resultant force" in document.blocks[0].text
        assert document.word_count > 0
