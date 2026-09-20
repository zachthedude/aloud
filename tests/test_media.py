"""Recordings as study material.

A lecture recording is the most common thing a student has and cannot study
from on the move. These cover the path it takes: accepted by signature, split
into passages where the speaker paused, and refused honestly when the server
cannot transcribe.

The transcription itself is exercised in one test that needs a real speech
provider, and skips cleanly when there is none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.models import ResourceKind
from app.services.documents import pipeline
from app.services.documents.base import UnreadableDocument
from app.services.documents.media import (
    MEDIA_EXTENSIONS,
    MediaProcessor,
    _looks_like_setup_failure,
    group_segments,
)


class FakeSpeech:
    """A speech provider that returns a fixed transcript."""

    available = True

    def __init__(self, segments):
        self.segments = segments
        self.calls = []

    def transcribe_file(self, path):
        self.calls.append(path)
        return self.segments


class BrokenSpeech:
    available = True

    def __init__(self, error: Exception) -> None:
        self.error = error

    def transcribe_file(self, path):
        raise self.error


# ---------------------------------------------------------------------------
# What gets accepted
# ---------------------------------------------------------------------------


class TestMediaValidation:
    @pytest.mark.parametrize(
        "filename,head,kind",
        [
            ("lecture.mp4", b"\x00\x00\x00\x20ftypisom" + b"\x00" * 40, ResourceKind.VIDEO),
            ("seminar.mov", b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 40, ResourceKind.VIDEO),
            ("class.webm", b"\x1a\x45\xdf\xa3" + b"\x00" * 40, ResourceKind.VIDEO),
            ("notes.mp3", b"ID3\x04\x00\x00" + b"\x00" * 40, ResourceKind.AUDIO),
            ("notes.mp3", b"\xff\xfb\x90\x64" + b"\x00" * 40, ResourceKind.AUDIO),
            ("talk.ogg", b"OggS\x00\x02" + b"\x00" * 40, ResourceKind.AUDIO),
            ("talk.flac", b"fLaC\x00\x00" + b"\x00" * 40, ResourceKind.AUDIO),
        ],
    )
    def test_real_recordings_are_accepted(self, filename, head, kind):
        extension = pipeline.check_name(filename)
        pipeline.check_signature(extension, head)
        assert pipeline.kind_for(extension) is kind
        assert pipeline.is_media(extension)

    def test_a_document_renamed_as_video_is_rejected(self):
        """The browser's filename is a claim; the bytes are the evidence.

        Either rejection wording is right here: the bytes announce themselves
        as a PDF, and the name claims otherwise.
        """
        with pytest.raises(pipeline.UploadRejected):
            pipeline.check_signature(".mp4", b"%PDF-1.4 this is really a pdf")

    def test_a_video_renamed_as_a_pdf_is_rejected(self):
        with pytest.raises(pipeline.UploadRejected):
            pipeline.check_signature(".pdf", b"\x00\x00\x00\x20ftypisom")

    def test_documents_are_not_treated_as_media(self):
        assert not pipeline.is_media(".pdf")
        assert not pipeline.is_media(".docx")

    def test_every_media_extension_maps_to_a_kind(self):
        for extension in MEDIA_EXTENSIONS:
            assert pipeline.kind_for(extension) in (ResourceKind.VIDEO, ResourceKind.AUDIO)


class TestStreamingStore:
    """Large uploads are written as they arrive, never buffered whole."""

    def test_a_recording_is_written_to_disk(self, tmp_path: Path):
        chunks = [b"x" * 1000, b"y" * 1000]
        path, size = pipeline.store_stream(
            iter(chunks), resource_id="res_a", extension=".mp3",
            directory=tmp_path, max_bytes=10_000,
        )
        assert size == 2000
        assert path.read_bytes() == b"x" * 1000 + b"y" * 1000
        assert path.name == "res_a.mp3", "the client's filename never reaches the disk"

    def test_the_cap_is_enforced_mid_stream(self, tmp_path: Path):
        """The point of streaming: stop early rather than after buffering it all."""
        def endless():
            while True:
                yield b"x" * 1024

        with pytest.raises(pipeline.UploadRejected, match="larger than"):
            pipeline.store_stream(
                endless(), resource_id="res_b", extension=".mp4",
                directory=tmp_path, max_bytes=4096,
            )

    def test_an_overrunning_file_is_not_left_behind(self, tmp_path: Path):
        def endless():
            while True:
                yield b"x" * 1024

        with pytest.raises(pipeline.UploadRejected):
            pipeline.store_stream(
                endless(), resource_id="res_c", extension=".mp4",
                directory=tmp_path, max_bytes=2048,
            )
        assert not (tmp_path / "res_c.mp4").exists()

    def test_an_empty_stream_is_rejected(self, tmp_path: Path):
        with pytest.raises(pipeline.UploadRejected, match="empty"):
            pipeline.store_stream(
                iter([]), resource_id="res_d", extension=".mp3",
                directory=tmp_path, max_bytes=1000,
            )


# ---------------------------------------------------------------------------
# Turning a transcript into passages
# ---------------------------------------------------------------------------


class TestSegmentGrouping:
    def test_a_long_pause_starts_a_new_passage(self):
        """A gap is where a lecturer changed topic or took a question."""
        segments = [
            (0.0, 4.0, "Diffusion is the movement of particles from high to low concentration."),
            (4.2, 8.0, "It continues until equilibrium is reached across the membrane."),
            (14.0, 18.0, "Osmosis is different. It refers specifically to water molecules."),
        ]
        passages = group_segments(segments)
        assert len(passages) == 2
        assert "Diffusion" in passages[0]
        assert "Osmosis" in passages[1]

    def test_a_short_breath_does_not_split(self):
        segments = [
            (0.0, 4.0, "Diffusion is the net movement of particles down a gradient."),
            (4.3, 8.0, "This continues until the concentrations are equal everywhere."),
        ]
        assert len(group_segments(segments)) == 1

    def test_an_unbroken_monologue_is_still_split(self):
        """Otherwise one long recording becomes a single unusable passage."""
        segments = [
            (i * 4.0, i * 4.0 + 3.9, "The particles move down the concentration gradient steadily. ")
            for i in range(40)
        ]
        passages = group_segments(segments)
        assert len(passages) > 1
        assert all(len(p.split()) <= 260 for p in passages)

    def test_scraps_are_dropped(self):
        assert group_segments([(0.0, 1.0, "Um. Right.")]) == []

    def test_empty_input_is_safe(self):
        assert group_segments([]) == []


# ---------------------------------------------------------------------------
# The processor
# ---------------------------------------------------------------------------


class TestMediaProcessor:
    def test_a_recording_becomes_passages(self, tmp_path: Path):
        path = tmp_path / "lecture.mp4"
        path.write_bytes(b"\x00\x00\x00\x20ftypisom")
        speech = FakeSpeech([
            (0.0, 5.0, "Photosynthesis is how plants turn light energy into chemical energy."),
            (11.0, 16.0, "Chlorophyll is the green pigment that absorbs the light in the chloroplast."),
        ])
        document = MediaProcessor(speech).extract(path)
        assert len(document.blocks) == 2
        assert document.title == "lecture"
        assert speech.calls == [path]

    def test_video_and_audio_are_both_recognised(self, tmp_path: Path):
        processor = MediaProcessor(FakeSpeech([]))
        assert processor.can_handle(tmp_path / "a.mp4")
        assert processor.can_handle(tmp_path / "a.m4a")
        assert not processor.can_handle(tmp_path / "a.pdf")

    def test_a_mime_type_is_enough_to_recognise_it(self, tmp_path: Path):
        processor = MediaProcessor(FakeSpeech([]))
        assert processor.can_handle(tmp_path / "recording", "video/mp4")

    def test_without_a_speech_provider_it_says_so_plainly(self, tmp_path: Path):
        path = tmp_path / "lecture.mp4"
        path.write_bytes(b"\x00\x00\x00\x20ftypisom")
        with pytest.raises(UnreadableDocument, match="not set up on this server"):
            MediaProcessor(None).extract(path)

    def test_silence_is_reported_as_silence(self, tmp_path: Path):
        path = tmp_path / "quiet.mp3"
        path.write_bytes(b"ID3\x04")
        with pytest.raises(UnreadableDocument, match="No speech"):
            MediaProcessor(FakeSpeech([])).extract(path)

    def test_a_setup_failure_does_not_blame_the_student_s_file(self, tmp_path: Path):
        """Telling someone their file is damaged sends them to re-export a good one."""
        path = tmp_path / "lecture.mp4"
        path.write_bytes(b"\x00\x00\x00\x20ftypisom")
        broken = BrokenSpeech(ImportError("No module named 'cgi'"))
        with pytest.raises(UnreadableDocument, match="Your file is fine"):
            MediaProcessor(broken).extract(path)

    def test_a_genuinely_bad_file_is_reported_as_one(self, tmp_path: Path):
        path = tmp_path / "broken.mp4"
        path.write_bytes(b"\x00\x00\x00\x20ftypisom")
        broken = BrokenSpeech(ValueError("Invalid data found when processing input"))
        with pytest.raises(UnreadableDocument, match="damaged"):
            MediaProcessor(broken).extract(path)

    @pytest.mark.parametrize(
        "error,is_setup",
        [
            (ImportError("No module named 'cgi'"), True),
            (OSError("Failed to download model from huggingface"), True),
            (ValueError("Invalid data found when processing input"), False),
            (RuntimeError("moov atom not found"), False),
        ],
    )
    def test_setup_failures_are_told_apart_from_bad_files(self, error, is_setup):
        assert _looks_like_setup_failure(error) is is_setup


# ---------------------------------------------------------------------------
# The real thing
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_a_real_recording_becomes_a_lesson(tmp_path: Path):
    """Generate speech, transcribe it, and check a concept comes out.

    Skipped unless both a speech synthesiser and a speech recogniser are
    installed, since this is the one test that needs real audio.
    """
    gtts = pytest.importorskip("gtts", reason="needs gTTS to make a recording")
    from app.services.providers.speech import WhisperSTT

    stt = WhisperSTT("base")
    if not stt.available:
        pytest.skip("faster-whisper is not installed")
    if stt._cached_model_dir("base") is None:
        pytest.skip("no whisper model cached; this test does not download one")

    path = tmp_path / "lecture.mp3"
    try:
        gtts.gTTS(
            text=(
                "Photosynthesis is the process by which plants convert light energy "
                "into chemical energy. It takes place in the chloroplasts, which "
                "contain a green pigment called chlorophyll."
            ),
            lang="en",
            tld="co.uk",
        ).write_to_fp(path.open("wb"))
    except Exception:  # noqa: BLE001 - gTTS needs the network
        pytest.skip("could not reach the speech service to make a recording")

    document = MediaProcessor(stt).extract(path)
    assert document.word_count > 15
    assert "photosynthesis" in " ".join(b.text for b in document.blocks).lower()

    sections = pipeline.to_sections(document, resource_id="r1", space_id="s1")
    from app.services.providers.ondevice import OnDeviceEngine

    model = OnDeviceEngine().analyse(sections, subject_hint="Biology")
    assert model.concepts, "a transcript should still yield something to teach"
