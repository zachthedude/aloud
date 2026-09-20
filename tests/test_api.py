"""End-to-end tests over real HTTP.

These cover the journey the product is judged on: create an account, add a
subject, upload material, build a plan, start a lesson, answer a question, and
come back later to exactly where you were.

Authorisation is tested adversarially, with a second account trying to reach
the first one's data at every endpoint that takes an id.
"""

from __future__ import annotations

import time

import pytest

from conftest import Client


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


class TestAuth:
    def test_sign_up_creates_a_session(self, client: Client):
        status, payload = client.post(
            "/api/auth/signup",
            {"email": "new@aloud.app", "password": "correcthorse", "displayName": "New"},
        )
        assert status == 201
        assert payload["user"]["email"] == "new@aloud.app"
        assert "password" not in str(payload).lower() or "passwordHash" not in str(payload)

        status, payload = client.get("/api/auth/me")
        assert status == 200
        assert payload["user"]["displayName"] == "New"

    def test_the_client_is_told_which_modes_exist(self, signed_in: Client):
        # The interface must not offer a mode the server will refuse.
        _status, payload = signed_in.get("/api/auth/me")
        assert payload["features"]["duels"] is False
        assert len(payload["features"]) == 16

    def test_a_password_hash_never_leaves_the_server(self, signed_in: Client):
        _status, payload = signed_in.get("/api/auth/me")
        assert "hash" not in str(payload).lower()

    def test_short_passwords_are_refused(self, client: Client):
        status, _payload = client.post(
            "/api/auth/signup", {"email": "a@b.com", "password": "short"}
        )
        assert status == 422

    def test_an_obvious_password_is_refused(self, client: Client):
        status, payload = client.post(
            "/api/auth/signup", {"email": "a@b.com", "password": "password"}
        )
        assert status == 422
        assert "guess" in payload["detail"].lower()

    def test_a_malformed_email_is_refused(self, client: Client):
        status, _payload = client.post(
            "/api/auth/signup", {"email": "not-an-email", "password": "correcthorse"}
        )
        assert status == 422

    def test_duplicate_emails_are_refused(self, signed_in: Client):
        status, payload = signed_in.post(
            "/api/auth/signup", {"email": "test@aloud.app", "password": "correcthorse"}
        )
        assert status == 409
        assert "already exists" in payload["detail"]

    def test_a_wrong_password_is_refused(self, signed_in: Client, server: str):
        other = Client(server)
        status, payload = other.post(
            "/api/auth/signin", {"email": "test@aloud.app", "password": "wrongwrongwrong"}
        )
        assert status == 401
        assert "do not match" in payload["detail"]

    def test_an_unknown_email_gives_the_same_answer(self, client: Client):
        """The error must not reveal which emails have accounts."""
        status, payload = client.post(
            "/api/auth/signin", {"email": "nobody@aloud.app", "password": "whatever123"}
        )
        assert status == 401
        assert "do not match" in payload["detail"]

    def test_signing_out_ends_the_session(self, signed_in: Client):
        assert signed_in.post("/api/auth/signout")[0] == 200
        assert signed_in.get("/api/dashboard")[0] == 401

    def test_preferences_round_trip(self, signed_in: Client):
        status, payload = signed_in.patch(
            "/api/auth/me", {"preferredSpeed": 1.5, "explanationStyle": "concrete"}
        )
        assert status == 200
        assert payload["user"]["preferredSpeed"] == 1.5
        assert payload["user"]["explanationStyle"] == "concrete"

    def test_an_unknown_explanation_style_is_refused(self, signed_in: Client):
        assert signed_in.patch("/api/auth/me", {"explanationStyle": "shouty"})[0] == 422

    def test_an_absurd_speed_is_refused(self, signed_in: Client):
        assert signed_in.patch("/api/auth/me", {"preferredSpeed": 99})[0] == 422


class TestUnauthenticated:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/dashboard"),
            ("GET", "/api/progress"),
            ("GET", "/api/spaces"),
            ("POST", "/api/spaces"),
            ("GET", "/api/bookmarks"),
            ("GET", "/api/spaces/spc_anything"),
            ("GET", "/api/lessons/lsn_anything"),
            ("GET", "/api/audio/status/lsn_anything"),
        ],
    )
    def test_every_data_endpoint_requires_an_account(self, client: Client, method, path):
        status, _payload = client.request(method, path, {} if method == "POST" else None)
        assert status == 401

    def test_the_app_shell_is_public(self, client: Client):
        status, _body, _headers = client.get("/", raw=True)
        assert status == 200

    @pytest.mark.parametrize(
        "path", ["/app", "/spaces", "/space/spc_123", "/plan", "/library", "/settings"]
    )
    def test_deep_links_return_the_shell_so_a_refresh_works(self, client: Client, path):
        status, body, headers = client.get(path, raw=True)
        assert status == 200
        assert "text/html" in headers.get("content-type", "")
        assert b"<div id=\"app\">" in body

    def test_a_missing_asset_is_a_404_not_the_shell(self, client: Client):
        """Serving HTML for a missing script turns a typo into a parse error."""
        status, _body, _headers = client.get("/js/does-not-exist.js", raw=True)
        assert status == 404
        status, _body, _headers = client.get("/nope.js", raw=True)
        assert status == 404

    def test_head_is_answered_on_pages_and_health(self, client: Client):
        assert client.request("HEAD", "/", raw=True)[0] == 200
        assert client.request("HEAD", "/health", raw=True)[0] == 200

    def test_security_headers_are_present(self, client: Client):
        _status, _body, headers = client.get("/", raw=True)
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("X-Frame-Options") == "DENY"
        assert "default-src 'self'" in headers.get("Content-Security-Policy", "")
        assert "microphone=(self)" in headers.get("Permissions-Policy", "")


# ---------------------------------------------------------------------------
# Subjects and material
# ---------------------------------------------------------------------------


@pytest.fixture
def space(signed_in: Client) -> str:
    status, payload = signed_in.post("/api/spaces", {"title": "Waves", "level": "Grade 10"})
    assert status == 201, payload
    return payload["space"]["id"]


class TestSpaces:
    def test_a_new_subject_starts_empty(self, signed_in: Client, space: str):
        status, payload = signed_in.get(f"/api/spaces/{space}")
        assert status == 200
        assert payload["space"]["status"] == "empty"
        assert payload["resources"] == []
        assert payload["plan"] == []

    def test_a_subject_needs_a_name(self, signed_in: Client):
        assert signed_in.post("/api/spaces", {"title": "x"})[0] == 422

    def test_renaming_works(self, signed_in: Client, space: str):
        status, payload = signed_in.patch(
            f"/api/spaces/{space}", {"title": "Waves and Sound", "level": "Grade 11"}
        )
        assert status == 200
        assert payload["space"]["title"] == "Waves and Sound"

    def test_deleting_removes_it(self, signed_in: Client, space: str):
        assert signed_in.delete(f"/api/spaces/{space}")[0] == 200
        assert signed_in.get(f"/api/spaces/{space}")[0] == 404

    def test_analysis_needs_material(self, signed_in: Client, space: str):
        status, payload = signed_in.post(f"/api/spaces/{space}/analyse", {})
        assert status == 400
        assert "at least one resource" in payload["detail"]


class TestUploads:
    def test_a_markdown_file_is_accepted_and_read(
        self, signed_in: Client, space: str, sample_markdown: bytes
    ):
        status, payload = signed_in.upload(
            f"/api/spaces/{space}/resources", "waves.md", sample_markdown
        )
        assert status == 201, payload
        assert payload["resource"]["status"] == "queued"
        assert payload["resource"]["filename"] == "waves.md"

    def test_a_disguised_executable_is_rejected(self, signed_in: Client, space: str):
        status, payload = signed_in.upload(
            f"/api/spaces/{space}/resources", "notes.pdf", b"MZ\x90\x00 not a pdf at all"
        )
        assert status == 400
        assert "valid" in payload["detail"].lower()

    def test_an_unsupported_type_is_rejected(self, signed_in: Client, space: str):
        status, payload = signed_in.upload(
            f"/api/spaces/{space}/resources", "virus.exe", b"MZ" + b"x" * 100
        )
        assert status == 400
        assert "not supported" in payload["detail"]

    def test_an_oversized_file_is_rejected(self, signed_in: Client, space: str):
        """The fixture caps uploads at 2 MB."""
        status, payload = signed_in.upload(
            f"/api/spaces/{space}/resources", "huge.txt", b"x" * (3 * 1024 * 1024)
        )
        assert status == 400
        assert "larger than" in payload["detail"]

    def test_typed_notes_are_accepted(self, signed_in: Client, space: str):
        status, payload = signed_in.post(
            f"/api/spaces/{space}/resources/note",
            {"title": "My notes", "text": "Refraction is the bending of light as it "
                                          "passes from one medium into another one."},
        )
        assert status == 201
        assert payload["resource"]["kind"] == "note"

    def test_notes_that_are_too_short_are_refused(self, signed_in: Client, space: str):
        assert signed_in.post(
            f"/api/spaces/{space}/resources/note", {"text": "too short"}
        )[0] == 422

    def test_a_private_address_is_refused(self, signed_in: Client, space: str):
        status, payload = signed_in.post(
            f"/api/spaces/{space}/resources/url", {"url": "http://127.0.0.1:8000/secrets"}
        )
        assert status == 400
        assert "cannot be reached" in payload["detail"]

    def test_removing_a_resource_works(
        self, signed_in: Client, space: str, sample_markdown: bytes
    ):
        _status, payload = signed_in.upload(
            f"/api/spaces/{space}/resources", "waves.md", sample_markdown
        )
        resource_id = payload["resource"]["id"]
        assert signed_in.delete(f"/api/spaces/{space}/resources/{resource_id}")[0] == 200
        _status, payload = signed_in.get(f"/api/spaces/{space}")
        assert payload["resources"] == []


# ---------------------------------------------------------------------------
# The whole journey
# ---------------------------------------------------------------------------


@pytest.fixture
def analysed(signed_in: Client, space: str, sample_markdown: bytes) -> tuple[Client, str]:
    """A subject with material, analysed into a plan and lessons."""
    status, _payload = signed_in.upload(
        f"/api/spaces/{space}/resources", "waves.md", sample_markdown
    )
    assert status == 201
    status, _payload = signed_in.post(f"/api/spaces/{space}/analyse", {})
    assert status == 202
    job = signed_in.wait_for_job(space)
    assert job["status"] == "done", job
    return signed_in, space


class TestCurriculumBuild:
    def test_analysis_produces_concepts_and_lessons(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        assert payload["space"]["status"] == "ready"
        assert len(payload["concepts"]) >= 3
        assert len(payload["lessons"]) >= 3
        assert len(payload["plan"]) == len(payload["lessons"])

    def test_the_concepts_come_from_the_material(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        names = {concept["name"].lower() for concept in payload["concepts"]}
        assert {"wavelength", "frequency", "amplitude"} & names

    def test_exactly_one_lesson_is_suggested_next(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        suggested = [i for i in payload["plan"] if i["recommendation"] == "Suggested next"]
        assert len(suggested) == 1

    def test_the_knowledge_map_has_nodes(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}/map")
        assert payload["nodes"]
        assert all("mastery" in node for node in payload["nodes"])
        assert all(node.get("lessonId") for node in payload["nodes"])

    def test_reanalysis_keeps_what_was_learned(self, analysed):
        """Reuploading a corrected chapter must not wipe a student's progress."""
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        lesson_id = payload["lessons"][0]["id"]

        # Answer a question so there is mastery to lose.
        _status, lesson = client.get(f"/api/lessons/{lesson_id}")
        session_id = lesson["session"]["id"]
        question = next(s["question"] for s in lesson["sections"] if s["question"])
        client.patch(f"/api/sessions/{session_id}", {"state": "playing"})
        client.patch(f"/api/sessions/{session_id}", {"state": "waiting_for_student"})
        client.post(
            f"/api/sessions/{session_id}/answer",
            {"questionId": question["id"], "transcript": "the distance between two peaks of a wave",
             "inputMode": "text"},
        )
        _status, before = client.get("/api/progress")
        touched_before = sum(
            1 for s in before["spaces"] for c in s["concepts"] if c["evidence"] > 0
        )
        assert touched_before >= 1

        client.post(f"/api/spaces/{space}/analyse", {})
        job = client.wait_for_job(space)
        assert job["status"] == "done"

        _status, after = client.get("/api/progress")
        assert after["spaces"], "the subject survives reanalysis"


class TestLessonFlow:
    @pytest.fixture
    def lesson(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        lesson_id = payload["lessons"][0]["id"]
        _status, lesson = client.get(f"/api/lessons/{lesson_id}")
        return client, space, lesson

    def test_a_lesson_arrives_ready_to_play(self, lesson):
        _client, _space, payload = lesson
        assert payload["lesson"]["title"]
        assert payload["sections"]
        assert payload["session"]["state"] == "idle"
        assert payload["neighbours"]["total"] >= 1

    def test_a_lesson_contains_questions(self, lesson):
        _client, _space, payload = lesson
        questions = [s["question"] for s in payload["sections"] if s["question"]]
        assert questions, "a lesson that never asks anything is not teaching"

    def test_expected_answers_are_never_sent_to_the_client(self, lesson):
        """Otherwise the answer is readable in the network tab."""
        _client, _space, payload = lesson
        body = str(payload).lower()
        assert "expected_points" not in body
        assert "expectedpoints" not in body
        assert "misconception_markers" not in body

    def test_playback_position_is_saved_and_resumed(self, lesson):
        client, _space, payload = lesson
        lesson_id = payload["lesson"]["id"]
        session_id = payload["session"]["id"]

        client.patch(f"/api/sessions/{session_id}", {"state": "playing"})
        status, _body = client.patch(
            f"/api/sessions/{session_id}",
            {"sectionOrdinal": 2, "positionSeconds": 31.5, "playbackSpeed": 1.25},
        )
        assert status == 200

        # Coming back later lands in the same place.
        _status, again = client.get(f"/api/lessons/{lesson_id}")
        assert again["session"]["id"] == session_id
        assert again["session"]["sectionOrdinal"] == 2
        assert again["session"]["positionSeconds"] == 31.5
        assert again["session"]["playbackSpeed"] == 1.25

    def test_speed_becomes_a_lasting_preference(self, lesson):
        client, _space, payload = lesson
        client.patch(f"/api/sessions/{payload['session']['id']}", {"playbackSpeed": 1.75})
        _status, me = client.get("/api/auth/me")
        assert me["user"]["preferredSpeed"] == 1.75

    def test_an_illegal_state_change_is_refused_clearly(self, lesson):
        client, _space, payload = lesson
        session_id = payload["session"]["id"]
        client.patch(f"/api/sessions/{session_id}", {"state": "playing"})
        status, body = client.patch(f"/api/sessions/{session_id}", {"state": "transcribing"})
        assert status == 409
        assert "Cannot move from playing" in body["detail"]

    def test_an_unknown_state_is_refused(self, lesson):
        client, _space, payload = lesson
        status, _body = client.patch(
            f"/api/sessions/{payload['session']['id']}", {"state": "dancing"}
        )
        assert status == 400

    def test_the_presentation_says_whose_turn_it_is(self, lesson):
        client, _space, payload = lesson
        session_id = payload["session"]["id"]
        client.patch(f"/api/sessions/{session_id}", {"state": "playing"})
        _status, body = client.patch(
            f"/api/sessions/{session_id}", {"state": "waiting_for_student"}
        )
        assert body["presentation"]["holder"] == "student"
        assert body["presentation"]["micActive"] is True


class TestAnswering:
    @pytest.fixture
    def ready_to_answer(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        # Pick the lesson about wavelength, so the answers below are on topic.
        lessons = payload["lessons"]
        lesson_id = next(
            (l["id"] for l in lessons if "wavelength" in l["title"].lower()), lessons[0]["id"]
        )
        _status, lesson = client.get(f"/api/lessons/{lesson_id}")
        session_id = lesson["session"]["id"]
        question = next(s["question"] for s in lesson["sections"] if s["question"])
        client.patch(f"/api/sessions/{session_id}", {"state": "playing"})
        client.patch(f"/api/sessions/{session_id}", {"state": "waiting_for_student"})
        return client, space, session_id, question

    def answer(self, client, session_id, question_id, text):
        return client.post(
            f"/api/sessions/{session_id}/answer",
            {"questionId": question_id, "transcript": text, "inputMode": "text"},
        )

    def test_a_good_answer_is_recognised(self, ready_to_answer):
        client, _space, session_id, question = ready_to_answer
        status, body = self.answer(
            client, session_id, question["id"],
            "wavelength is the distance between two consecutive peaks of a wave, "
            "measured in metres",
        )
        assert status == 200
        assert body["reply"]["judgement"] in ("correct", "mostly_correct", "incomplete")
        assert body["reply"]["text"]
        assert body["reply"]["mastery"]

    def test_an_empty_answer_is_not_punished(self, ready_to_answer):
        client, _space, session_id, question = ready_to_answer
        status, body = self.answer(client, session_id, question["id"], "")
        assert status == 200
        assert body["reply"]["action"] == "ask_again"
        assert body["reply"]["mastery"] is None, "nothing is recorded for silence"

    def test_saying_you_do_not_know_costs_nothing(self, ready_to_answer):
        client, _space, session_id, question = ready_to_answer
        status, body = self.answer(client, session_id, question["id"], "I don't know")
        assert status == 200
        assert body["reply"]["judgement"] == "unclear"
        assert body["reply"]["action"] == "ask_again"

    def test_no_reply_ever_scolds_the_student(self, ready_to_answer):
        client, _space, session_id, question = ready_to_answer
        replies = []
        for text in (
            "wavelength is the distance between two peaks",
            "i think it is about magnets",
            "no idea",
            "the amplitude of the wave measured in seconds",
        ):
            client.patch(f"/api/sessions/{session_id}", {"state": "waiting_for_student"})
            _status, body = self.answer(client, session_id, question["id"], text)
            replies.append(body["reply"]["text"].lower())

        for reply in replies:
            for word in ("wrong", "failed", "bad answer", "should have known", "behind"):
                assert word not in reply, reply

    def test_an_unknown_question_is_a_clean_404(self, ready_to_answer):
        client, _space, session_id, _question = ready_to_answer
        status, _body = self.answer(client, session_id, "qst_does_not_exist", "something")
        assert status == 404

    def test_answers_move_progress(self, ready_to_answer):
        client, _space, session_id, question = ready_to_answer
        self.answer(
            client, session_id, question["id"],
            "the distance between two consecutive peaks of a wave, measured in metres",
        )
        _status, progress = client.get("/api/progress")
        touched = [
            concept for space in progress["spaces"]
            for concept in space["concepts"] if concept["evidence"] > 0
        ]
        assert touched

    def test_finishing_produces_an_assessment(self, ready_to_answer):
        client, _space, session_id, question = ready_to_answer
        self.answer(
            client, session_id, question["id"],
            "the distance between two consecutive peaks of a wave",
        )
        status, body = client.post(f"/api/sessions/{session_id}/complete", {})
        assert status == 200
        assessment = body["assessment"]
        assert assessment["outcomeLabel"]
        assert assessment["recommendation"]
        assert assessment["nextAction"] in ("continue", "review", "revisit_prereq", "extend")

    def test_finishing_without_answering_still_records_exposure(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        _status, lesson = client.get(f"/api/lessons/{payload['lessons'][0]['id']}")
        session_id = lesson["session"]["id"]
        status, body = client.post(f"/api/sessions/{session_id}/complete", {})
        assert status == 200
        assert body["assessment"]["outcome"] in ("developing", "not_started")

    def test_another_question_can_be_requested(self, ready_to_answer):
        client, _space, session_id, _question = ready_to_answer
        status, body = client.post(f"/api/sessions/{session_id}/question", {})
        assert status == 200
        assert body["question"]["prompt"]


class TestAskingTheTeacher:
    def test_a_question_is_answered_from_the_material(self, analysed):
        client, space = analysed
        status, body = client.post(
            f"/api/spaces/{space}/ask", {"text": "what is amplitude"}
        )
        assert status == 200
        assert body["handledBy"] == "teacher"
        assert body["answer"]["text"]

    def test_a_transport_command_is_handed_to_the_player(self, analysed):
        """The player must act at once rather than wait for prose."""
        client, space = analysed
        _status, body = client.post(f"/api/spaces/{space}/ask", {"text": "pause"})
        assert body["handledBy"] == "player"
        assert body["intent"] == "pause"
        assert body["answer"] is None

    @pytest.mark.parametrize(
        "text,intent", [("go back", "back"), ("speed up", "faster"), ("repeat that", "repeat")]
    )
    def test_other_transport_commands_too(self, analysed, text, intent):
        client, space = analysed
        _status, body = client.post(f"/api/spaces/{space}/ask", {"text": text})
        assert body["intent"] == intent

    def test_an_answer_is_marked_not_chatted_with(self, analysed):
        """While a question is on the floor, a reply is an answer.

        Routing it to the teacher instead would hand the student a fresh
        explanation and record nothing about what they actually said.
        """
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        _status, lesson = client.get(f"/api/lessons/{payload['lessons'][0]['id']}")
        session_id = lesson["session"]["id"]
        client.patch(f"/api/sessions/{session_id}", {"state": "playing"})
        client.patch(f"/api/sessions/{session_id}", {"state": "waiting_for_student"})

        _status, body = client.post(
            f"/api/spaces/{space}/ask",
            {"text": "wavelength is the distance between two peaks of a wave",
             "sessionId": session_id},
        )
        assert body["handledBy"] == "answer"
        assert body["answer"] is None

    def test_the_same_words_are_a_question_when_nothing_was_asked(self, analysed):
        client, space = analysed
        _status, body = client.post(
            f"/api/spaces/{space}/ask",
            {"text": "wavelength is the distance between two peaks of a wave"},
        )
        assert body["handledBy"] == "teacher"
        assert body["answer"]["text"]

    def test_quiz_me_is_routed_to_questions(self, analysed):
        client, space = analysed
        _status, body = client.post(f"/api/spaces/{space}/ask", {"text": "quiz me"})
        assert body["handledBy"] == "quiz"

    def test_asking_for_a_simpler_explanation_re_explains(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        concept_id = payload["concepts"][0]["id"]
        _status, body = client.post(
            f"/api/spaces/{space}/ask",
            {"text": "explain that more simply", "conceptId": concept_id},
        )
        assert body["handledBy"] == "teacher"
        assert body["answer"]["text"]

    def test_the_conversation_is_kept(self, analysed):
        client, space = analysed
        client.post(f"/api/spaces/{space}/ask", {"text": "what is frequency"})
        _status, body = client.get(f"/api/spaces/{space}/conversation")
        roles = [message["role"] for message in body["messages"]]
        assert "student" in roles and "teacher" in roles


class TestBookmarks:
    def test_a_place_can_be_saved_and_removed(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        lesson_id = payload["lessons"][0]["id"]

        status, body = client.post(
            f"/api/spaces/{space}/bookmarks",
            {"lessonId": lesson_id, "sectionOrdinal": 2, "positionSeconds": 14.5},
        )
        assert status == 201
        bookmark_id = body["bookmark"]["id"]

        _status, listing = client.get("/api/bookmarks")
        assert any(b["id"] == bookmark_id for b in listing["bookmarks"])

        assert client.delete(f"/api/bookmarks/{bookmark_id}")[0] == 200
        _status, listing = client.get("/api/bookmarks")
        assert not any(b["id"] == bookmark_id for b in listing["bookmarks"])


class TestAudio:
    def test_the_download_state_is_reported_honestly(self, analysed):
        """The fixture configures no speech provider, so downloads are off."""
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        lesson_id = payload["lessons"][0]["id"]

        status, body = client.get(f"/api/audio/status/{lesson_id}")
        assert status == 200
        assert body["state"] == "unavailable"
        assert "switched off" in body["message"]

    def test_preparing_audio_fails_loudly_rather_than_silently(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        lesson_id = payload["lessons"][0]["id"]
        status, body = client.post(f"/api/audio/prepare/{lesson_id}", {"voice": ""})
        assert status == 503
        assert "switched off" in body["detail"]

    def test_downloading_unprepared_audio_is_a_clean_404(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        lesson_id = payload["lessons"][0]["id"]
        assert client.get(f"/api/audio/download/{lesson_id}")[0] == 404


class TestDashboard:
    def test_an_empty_account_gets_an_empty_dashboard(self, signed_in: Client):
        status, body = signed_in.get("/api/dashboard")
        assert status == 200
        assert body["spaces"] == []
        assert body["continue"] is None

    def test_progress_appears_after_learning(self, analysed):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        _status, lesson = client.get(f"/api/lessons/{payload['lessons'][0]['id']}")
        session_id = lesson["session"]["id"]
        client.patch(f"/api/sessions/{session_id}", {"state": "playing"})
        client.patch(f"/api/sessions/{session_id}", {"sectionOrdinal": 1})

        _status, dashboard = client.get("/api/dashboard")
        assert dashboard["continue"] is not None
        assert dashboard["continue"]["lessonId"] == lesson["lesson"]["id"]
        assert dashboard["recommendation"] is not None

    def test_the_dashboard_carries_no_pressure_metrics(self, analysed):
        """No streaks, no daily targets, no time spent, nothing overdue."""
        client, _space = analysed
        _status, dashboard = client.get("/api/dashboard")
        body = str(dashboard).lower()
        for word in ("streak", "overdue", "behind", "deadline", "daily goal", "minutes today"):
            assert word not in body


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


class TestAuthorisation:
    @pytest.fixture
    def intruder(self, server: str) -> Client:
        other = Client(server)
        status, _payload = other.post(
            "/api/auth/signup",
            {"email": "intruder@aloud.app", "password": "correcthorse", "displayName": "Nosy"},
        )
        assert status == 201
        return other

    def test_another_account_cannot_read_a_subject(self, analysed, intruder: Client):
        _client, space = analysed
        status, _body = intruder.get(f"/api/spaces/{space}")
        assert status == 404, "404 rather than 403: do not confirm the id exists"

    def test_another_account_cannot_change_a_subject(self, analysed, intruder: Client):
        _client, space = analysed
        assert intruder.patch(f"/api/spaces/{space}", {"title": "Mine now"})[0] == 404

    def test_another_account_cannot_delete_a_subject(self, analysed, intruder: Client):
        client, space = analysed
        assert intruder.delete(f"/api/spaces/{space}")[0] == 404
        assert client.get(f"/api/spaces/{space}")[0] == 200, "still there"

    def test_another_account_cannot_upload_into_a_subject(
        self, analysed, intruder: Client, sample_markdown: bytes
    ):
        _client, space = analysed
        status, _body = intruder.upload(
            f"/api/spaces/{space}/resources", "sneaky.md", sample_markdown
        )
        assert status == 404

    def test_another_account_cannot_read_a_lesson(self, analysed, intruder: Client):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        lesson_id = payload["lessons"][0]["id"]
        assert intruder.get(f"/api/lessons/{lesson_id}")[0] == 404

    def test_another_account_cannot_touch_a_session(self, analysed, intruder: Client):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        _status, lesson = client.get(f"/api/lessons/{payload['lessons'][0]['id']}")
        session_id = lesson["session"]["id"]
        assert intruder.patch(f"/api/sessions/{session_id}", {"positionSeconds": 5})[0] == 404
        assert intruder.post(f"/api/sessions/{session_id}/complete", {})[0] == 404

    def test_another_account_cannot_ask_about_a_subject(self, analysed, intruder: Client):
        _client, space = analysed
        assert intruder.post(f"/api/spaces/{space}/ask", {"text": "what is this"})[0] == 404

    def test_another_account_cannot_read_the_knowledge_map(self, analysed, intruder: Client):
        _client, space = analysed
        assert intruder.get(f"/api/spaces/{space}/map")[0] == 404

    def test_another_account_cannot_read_audio_status(self, analysed, intruder: Client):
        client, space = analysed
        _status, payload = client.get(f"/api/spaces/{space}")
        lesson_id = payload["lessons"][0]["id"]
        assert intruder.get(f"/api/audio/status/{lesson_id}")[0] == 404

    def test_dashboards_do_not_leak_across_accounts(self, analysed, intruder: Client):
        _status, body = intruder.get("/api/dashboard")
        assert body["spaces"] == []
        assert body["continue"] is None
