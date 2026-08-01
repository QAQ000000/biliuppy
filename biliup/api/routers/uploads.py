from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from biliup.database.models import UploadStreamer
from biliup.integrations.uploader import upload_files

from ..context import AppContext
from ..dependencies import get_context, get_session
from ..schemas import ManualUploadInput, UploadStreamerInput, orm_dict

router = APIRouter()


@router.get("/v1/upload/streamers")
def list_templates(session: Session = Depends(get_session)) -> list[dict]:
    return [orm_dict(row) for row in session.scalars(select(UploadStreamer).order_by(UploadStreamer.id))]


@router.get("/v1/upload/streamers/{template_id}")
def get_template(template_id: int, session: Session = Depends(get_session)) -> dict:
    row = session.get(UploadStreamer, template_id)
    if not row:
        raise HTTPException(404, "upload template not found")
    return orm_dict(row)


@router.post("/v1/upload/streamers")
def save_template(payload: UploadStreamerInput, session: Session = Depends(get_session)) -> dict:
    values = payload.model_dump(exclude={"id"})
    if payload.id is None:
        row = UploadStreamer(**values)
        session.add(row)
    else:
        row = session.get(UploadStreamer, payload.id)
        if not row:
            raise HTTPException(404, "upload template not found")
        for key, value in values.items():
            setattr(row, key, value)
    session.commit()
    session.refresh(row)
    return orm_dict(row)


@router.delete("/v1/upload/streamers/{template_id}", status_code=204)
def delete_template(template_id: int, session: Session = Depends(get_session)) -> Response:
    row = session.get(UploadStreamer, template_id)
    if not row:
        raise HTTPException(404, "upload template not found")
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.post("/v1/uploads")
async def manual_upload(payload: ManualUploadInput, context: AppContext = Depends(get_context)) -> dict:
    params = payload.params.model_dump()
    for key in ("submit_api", "lines", "threads"):
        params[key] = context.config.get(key)
    params["user"] = context.config.get("user", {})
    job = context.jobs.submit(
        "upload",
        upload_files(payload.files, params, context.paths),
    )
    return {"accepted": True, "task": job.id}


@router.get("/v1/uploads/{job_id}")
def upload_status(job_id: str, context: AppContext = Depends(get_context)) -> dict:
    job = context.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "upload job not found")
    return job.as_dict()
