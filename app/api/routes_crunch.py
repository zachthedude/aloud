"""Crunch sittings over HTTP.

Four calls, the same for every mode: start one, send a reply, end it, and read
it back after a closed tab. The client never learns anything mode-specific
from this API beyond what to say and whether to listen, which is what keeps
the room one screen for all sixteen modes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..domain.models import CrunchSession, User
from ..domain.turn import Reply, Turn
from ..services.crunch_service import CrunchNotFound, CrunchUnavailable
from ..services.modes.catalogue import BY_KEY as CATALOGUE_BY_KEY
from .deps import Container, current_user, get_container
from .schemas import CrunchStartRequest, CrunchTurnRequest

router = APIRouter(prefix="/api/crunch", tags=["crunch"])


def serialise_turn(turn: Turn) -> dict:
    return {
        "say": turn.say,
        "expects": turn.expects,
        "conceptId": turn.concept_id,
        "questionId": turn.question_id,
        "groundedIn": list(turn.grounded_in),
        "done": turn.done,
    }


def serialise_crunch(session: CrunchSession) -> dict:
    mode = CATALOGUE_BY_KEY.get(session.mode)
    return {
        "id": session.id,
        "spaceId": session.space_id,
        "mode": session.mode,
        "modeName": mode.name if mode else session.mode,
        "modeIcon": mode.icon if mode else "",
        "ended": session.ended,
        # The cursor is the mode's private memory and is not sent: it holds
        # question ids, and the client has no use for it.
    }


def last_turn(session: CrunchSession) -> Turn:
    return Turn(
        say=session.last_say,
        expects=session.last_expects,
        concept_id=session.last_concept_id,
        question_id=session.last_question_id,
        grounded_in=tuple(session.last_grounded_in),
        done=session.ended,
    )


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, CrunchNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


@router.post("", status_code=status.HTTP_201_CREATED)
def start(
    payload: CrunchStartRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    try:
        session, turn = container.crunch.start(user, payload.space_id, payload.mode)
    except (CrunchNotFound, CrunchUnavailable) as error:
        raise _translate(error) from None
    return {"session": serialise_crunch(session), "turn": serialise_turn(turn)}


@router.get("/{session_id}")
def read(
    session_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    try:
        session = container.crunch.get(user, session_id)
    except CrunchNotFound as error:
        raise _translate(error) from None
    return {"session": serialise_crunch(session), "turn": serialise_turn(last_turn(session))}


@router.post("/{session_id}/turn")
def turn(
    session_id: str,
    payload: CrunchTurnRequest,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    reply = Reply(
        text=payload.text,
        action=payload.action,
        input_mode=payload.input_mode,
        latency_ms=payload.latency_ms,
    )
    try:
        session, next_turn = container.crunch.reply(user, session_id, reply)
    except (CrunchNotFound, CrunchUnavailable) as error:
        raise _translate(error) from None
    return {"session": serialise_crunch(session), "turn": serialise_turn(next_turn)}


@router.post("/{session_id}/end")
def end(
    session_id: str,
    user: User = Depends(current_user),
    container: Container = Depends(get_container),
) -> dict:
    try:
        session = container.crunch.end(user, session_id)
    except CrunchNotFound as error:
        raise _translate(error) from None
    return {"session": serialise_crunch(session)}
