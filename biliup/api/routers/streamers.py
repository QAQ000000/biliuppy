from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from biliup.database.models import LiveStreamer

from ..context import AppContext
from ..dependencies import get_context, get_session
from ..schemas import LiveStreamerInput, PauseStreamerInput, orm_dict

router = APIRouter()


def _response(streamer: LiveStreamer, context: AppContext) -> dict:
    value = orm_dict(streamer)
    worker = context.scheduler.workers.get(streamer.id)
    value["status"] = worker.status if worker else "Pending"
    value["upload_status"] = worker.upload_status if worker else "Idle"
    value["paused"] = worker.paused if worker else False
    return value


@router.get("/v1/streamers")
def list_streamers(
    session: Session = Depends(get_session), context: AppContext = Depends(get_context)
) -> list[dict]:
    rows = session.scalars(select(LiveStreamer).order_by(LiveStreamer.id)).all()
    return [_response(row, context) for row in rows]


@router.post("/v1/streamers")
async def add_streamer(
    payload: LiveStreamerInput,
    session: Session = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> dict:
    values = payload.model_dump(exclude={"id"})
    streamer = LiveStreamer(**values)
    session.add(streamer)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "直播间 URL 已存在") from exc
    session.refresh(streamer)
    await context.scheduler.reload()
    return _response(streamer, context)


@router.put("/v1/streamers")
async def update_streamer(
    payload: LiveStreamerInput,
    session: Session = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> dict:
    if payload.id is None:
        raise HTTPException(422, "id is required")
    streamer = session.get(LiveStreamer, payload.id)
    if not streamer:
        raise HTTPException(404, "streamer not found")
    for key, value in payload.model_dump(exclude={"id"}).items():
        setattr(streamer, key, value)
    session.commit()
    session.refresh(streamer)
    await context.scheduler.remove(streamer.id)
    await context.scheduler.reload()
    return _response(streamer, context)


@router.delete("/v1/streamers/{streamer_id}", status_code=204)
async def delete_streamer(
    streamer_id: int,
    session: Session = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> Response:
    streamer = session.get(LiveStreamer, streamer_id)
    if not streamer:
        raise HTTPException(404, "streamer not found")
    await context.scheduler.remove(streamer_id)
    session.delete(streamer)
    session.commit()
    return Response(status_code=204)


@router.put("/v1/streamers/{streamer_id}/pause")
async def pause_streamer(
    streamer_id: int,
    payload: PauseStreamerInput | None = None,
    context: AppContext = Depends(get_context),
) -> dict:
    try:
        if payload is None:
            state = await context.scheduler.toggle_pause(streamer_id)
        else:
            state = await context.scheduler.set_paused(streamer_id, payload.paused)
    except KeyError as exc:
        raise HTTPException(404, "streamer not found") from exc
    return {"id": streamer_id, "paused": state.paused, "status": state.status}
