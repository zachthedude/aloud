"""Playing a lesson: sessions, playback, answers, and finishing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..domain.lesson_state import IllegalTransition, presentation
from ..domain.models import Bookmark, LessonState, User, new_id
from ..domain.voice_commands import LOCAL_INTENTS, Intent, parse
from ..services.lesson_service import LessonNotFound
from .deps import Container, current_user, get_container
from .schemas import (
    AnswerRequest,
    AskRequest,
    BookmarkRequest,
    PlaybackRequest,
    serialise_assessment,
    serialise_bookmark,
    serialise_lesson,
    serialise_lesson_section,
    serialise_question,
    serialise_session,
)

router = APIRouter(prefix="/api", tags=["lessons"])


def _load_session(container: Container, user: User, session_id: str):
    session = container.repos.sessions.get(session_id, user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="That lesson session could not be found.")
    return session


def _owned_lesson(container: Container, user: User, lesson_id: str):
    lesson = container.repos.lessons.get(lesson_id)
    if lesson is None or container.repos.spaces.get(lesson.space_id, user.id) is None:
        raise HTTPException(status_code=404, detail="That lesson could not be found.")
    return lesson


@router.get("/lessons/{lesson_id}")
def get_lesson(
    lesson_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Everything needed to play a lesson, including where to resume."""
    lesson = _owned_lesson(container, user, lesson_id)
    payload = container.lessons.lesson_payload(lesson)
    session = container.lessons.start_or_resume(user.id, lesson_id)

    voice = user.preferred_voice or container.audio.default_voice()
    assets = {a.lesson_section_id: a for a in container.repos.audio.for_lesson(lesson_id, voice)}

    concept = container.repos.concepts.get(lesson.concept_id)
    mastery = container.repos.mastery.get(user.id, lesson.concept_id)

    sections = []
    for section in payload["sections"]:
        asset = assets.get(section.id)
        audio = None
        if asset and asset.status.value == "ready":
            audio = {
                "url": f"/api/audio/section/{section.id}?voice={voice}",
                "durationSeconds": asset.duration_seconds,
                "mimeType": asset.mime_type,
            }
        sections.append(
            serialise_lesson_section(section, payload["questions"].get(section.question_id), audio)
        )

    return {
        "lesson": serialise_lesson(lesson, mastery=mastery.state if mastery else None),
        "sections": sections,
        "session": serialise_session(session),
        "concept": {
            "id": concept.id,
            "name": concept.name,
            "terminology": concept.terminology,
            "formulas": concept.formulas,
        } if concept else None,
        "audio": container.audio.lesson_status(lesson_id, voice),
        "neighbours": _neighbours(container, lesson),
    }


def _neighbours(container: Container, lesson) -> dict:
    """The lessons either side, so the player has a queue."""
    lessons = container.repos.lessons.list_for_space(lesson.space_id)
    index = next((i for i, l in enumerate(lessons) if l.id == lesson.id), -1)
    def brief(item):
        return {"id": item.id, "title": item.title} if item else None
    return {
        "previous": brief(lessons[index - 1]) if index > 0 else None,
        "next": brief(lessons[index + 1]) if 0 <= index < len(lessons) - 1 else None,
        "position": index + 1,
        "total": len(lessons),
    }


@router.patch("/sessions/{session_id}")
def update_playback(
    session_id: str,
    payload: PlaybackRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Save playback position and state, so the lesson resumes exactly."""
    session = _load_session(container, user, session_id)

    state = None
    if payload.state:
        try:
            state = LessonState(payload.state)
        except ValueError:
            raise HTTPException(status_code=400, detail="Unknown player state.") from None

    try:
        session = container.lessons.update_playback(
            session,
            state=state,
            section_ordinal=payload.section_ordinal,
            position_seconds=payload.position_seconds,
            playback_speed=payload.playback_speed,
        )
    except IllegalTransition as error:
        # A genuine client bug. Say what was expected rather than failing mutely.
        raise HTTPException(status_code=409, detail=str(error)) from None

    turn = presentation(session.state)
    return {
        "session": serialise_session(session),
        "presentation": {
            "holder": turn.holder,
            "headline": turn.headline,
            "hint": turn.hint,
            "micActive": turn.mic_active,
            "audioActive": turn.audio_active,
            "busy": turn.busy,
        },
    }


@router.post("/sessions/{session_id}/answer")
def submit_answer(
    session_id: str,
    payload: AnswerRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Read a spoken or typed answer and reply as the teacher."""
    session = _load_session(container, user, session_id)

    if not payload.transcript.strip():
        # Not an error. The student said nothing audible, so ask again.
        return {
            "reply": {
                "text": "I did not catch that. Take your time and say it again when you are ready.",
                "action": "ask_again",
                "judgement": "unclear",
                "confidence": 0.0,
                "matched": [],
                "missed": [],
                "mastery": None,
                "masteryLabel": None,
            },
            "session": serialise_session(session),
        }

    try:
        reply = container.lessons.submit_answer(
            session, payload.question_id, payload.transcript, input_mode=payload.input_mode
        )
    except LessonNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except IllegalTransition as error:
        raise HTTPException(status_code=409, detail=str(error)) from None

    analysis = reply.analysis
    return {
        "reply": {
            "text": reply.text,
            "action": reply.action,
            "judgement": analysis.judgement.value if analysis else "unclear",
            "confidence": analysis.confidence if analysis else 0.0,
            "matched": analysis.matched_points if analysis else [],
            "missed": analysis.missed_points if analysis else [],
            "mastery": reply.mastery.value if reply.mastery else None,
            "masteryLabel": reply.mastery.label if reply.mastery else None,
            "followUpQuestion": (
                serialise_question(reply.follow_up_question) if reply.follow_up_question else None
            ),
        },
        "session": serialise_session(
            container.repos.sessions.get(session_id, user.id) or session
        ),
    }


@router.post("/sessions/{session_id}/complete")
def complete_lesson(
    session_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Wrap up: takeaways, understanding state and what to do next."""
    session = _load_session(container, user, session_id)
    try:
        assessment = container.lessons.complete_lesson(session)
    except LessonNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except IllegalTransition as error:
        raise HTTPException(status_code=409, detail=str(error)) from None

    plan = container.curriculum.plan_for(session.space_id, user.id)
    from ..domain.curriculum import recommend_next
    from .schemas import serialise_recommendation

    return {
        "assessment": serialise_assessment(assessment),
        "recommendation": serialise_recommendation(recommend_next(plan)),
        "session": serialise_session(session),
    }


@router.post("/sessions/{session_id}/question")
def another_question(
    session_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Another question on the current concept, for "quiz me"."""
    session = _load_session(container, user, session_id)
    lesson = container.repos.lessons.get(session.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="That lesson could not be found.")

    asked = [r.question_id for r in container.repos.responses.for_session(session.id)]
    question = container.lessons.next_question(lesson.concept_id, exclude=asked)
    if question is None:
        # Everything has been asked once; cycle rather than refuse.
        question = container.lessons.next_question(lesson.concept_id)
    if question is None:
        raise HTTPException(
            status_code=404, detail="There are no questions on this concept yet."
        )

    session.pending_question_id = question.id
    container.repos.sessions.save(session)
    return {"question": serialise_question(question)}


# ---------------------------------------------------------------------------
# Asking the teacher
# ---------------------------------------------------------------------------


@router.post("/spaces/{space_id}/ask")
def ask_teacher(
    space_id: str,
    payload: AskRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    """Answer a question, or act on a voice command.

    Commands the client can carry out alone (pause, skip, speed) are reported
    back as intents rather than answered, so the player acts immediately
    instead of waiting for prose it does not need.
    """
    if container.repos.spaces.get(space_id, user.id) is None:
        raise HTTPException(status_code=404, detail="That subject could not be found.")

    session = None
    if payload.session_id:
        session = container.repos.sessions.get(payload.session_id, user.id)

    awaiting = bool(session and session.state is LessonState.WAITING_FOR_STUDENT)
    command = parse(payload.text, awaiting_answer=awaiting)

    if command.intent is Intent.ANSWER:
        # The student is answering the question on the floor, not asking one.
        # Say so plainly rather than replying, so the client sends it to be
        # marked instead of treating a graded answer as a chat message.
        return {"intent": command.intent.value, "handledBy": "answer", "answer": None}

    if command.intent in LOCAL_INTENTS:
        return {
            "intent": command.intent.value,
            "handledBy": "player",
            "answer": None,
        }

    if command.intent in (
        Intent.EXPLAIN_SIMPLER, Intent.EXPLAIN_DIFFERENTLY, Intent.GIVE_EXAMPLE,
        Intent.CONFUSED, Intent.DEFINE,
    ):
        answer = container.lessons.handle_command(
            user.id, space_id, command, session=session, concept_id=payload.concept_id
        )
    elif command.intent in (Intent.ASK_ME, Intent.QUIZ_ME):
        return {"intent": command.intent.value, "handledBy": "quiz", "answer": None}
    elif command.intent is Intent.REVIEW_LATER:
        return {"intent": command.intent.value, "handledBy": "bookmark", "answer": None}
    elif command.intent is Intent.MOVE_ON:
        return {"intent": "forward", "handledBy": "player", "answer": None}
    else:
        answer = container.lessons.ask(
            user.id, space_id, payload.text, session=session, concept_id=payload.concept_id
        )

    return {
        "intent": command.intent.value,
        "handledBy": "teacher",
        "answer": {
            "text": answer.text,
            "sourceNote": answer.source_note,
            "sourceIds": answer.source_section_ids,
            "usedOutsideKnowledge": answer.used_outside_knowledge,
        },
    }


@router.get("/spaces/{space_id}/conversation")
def conversation(
    space_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    if container.repos.spaces.get(space_id, user.id) is None:
        raise HTTPException(status_code=404, detail="That subject could not be found.")
    from .schemas import serialise_message

    messages = container.repos.messages.for_space(user.id, space_id, limit=50)
    return {"messages": [serialise_message(m) for m in messages]}


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------


@router.post("/spaces/{space_id}/bookmarks", status_code=status.HTTP_201_CREATED)
def add_bookmark(
    space_id: str,
    payload: BookmarkRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    if container.repos.spaces.get(space_id, user.id) is None:
        raise HTTPException(status_code=404, detail="That subject could not be found.")
    lesson = _owned_lesson(container, user, payload.lesson_id)

    bookmark = Bookmark(
        id=new_id("bkm"),
        user_id=user.id,
        space_id=space_id,
        lesson_id=payload.lesson_id,
        section_ordinal=payload.section_ordinal,
        position_seconds=payload.position_seconds,
        label=payload.label or lesson.title,
    )
    container.repos.bookmarks.add(bookmark)
    return {"bookmark": serialise_bookmark(bookmark, lesson.title)}


@router.get("/bookmarks")
def list_bookmarks(
    space_id: str = "",
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    bookmarks = container.repos.bookmarks.list_for_user(user.id, space_id)
    titles = {}
    for bookmark in bookmarks:
        if bookmark.lesson_id not in titles:
            lesson = container.repos.lessons.get(bookmark.lesson_id)
            titles[bookmark.lesson_id] = lesson.title if lesson else ""
    return {
        "bookmarks": [serialise_bookmark(b, titles.get(b.lesson_id, "")) for b in bookmarks]
    }


@router.delete("/bookmarks/{bookmark_id}")
def delete_bookmark(
    bookmark_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    if not container.repos.bookmarks.delete(bookmark_id, user.id):
        raise HTTPException(status_code=404, detail="That bookmark could not be found.")
    return {"ok": True}
