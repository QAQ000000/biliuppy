from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from biliup.database.models import FileItem, UploadStreamer
from biliup.integrations.uploader import resolve_upload_files, upload_files, upload_idempotency_key
from biliup.services import JobAdmissionClosedError, JobCapacityError
from biliup.services.upload_templates import render_upload_text

from ..context import AppContext
from ..dependencies import get_context, get_session
from ..schemas import ManualUploadInput, UploadStreamerInput, orm_dict

router = APIRouter()


def _manual_template_context(files: list[str], context: AppContext) -> dict:
    resolved = resolve_upload_files(files, context.paths)
    first = resolved[0]
    template_context = {
        "name": first.stem,
        "room_title": first.stem,
        "url": "",
        "start_time": int(first.stat().st_mtime),
    }
    with context.database.session_factory() as session:
        item = session.scalar(
            select(FileItem)
            .where(FileItem.file == str(first))
            .order_by(FileItem.id.desc())
        )
        if item is not None:
            info = item.streamer_info
            template_context.update(
                {
                    "name": info.name,
                    "room_title": info.title,
                    "url": info.url,
                    "start_time": int(info.date.timestamp()),
                }
            )
    return template_context


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
    for key in ("submit_api", "lines", "threads", "submit_interval"):
        params[key] = context.config.get(key)
    params["user"] = context.config.get("user", {})
    try:
        template_context = _manual_template_context(payload.files, context)
        params["title"] = render_upload_text(
            params.get("title") or template_context["room_title"],
            template_context,
        )
        params["description"] = render_upload_text(
            params.get("description") or "",
            template_context,
        )
        params["source_url"] = template_context["url"]
        idempotency_key = upload_idempotency_key(payload.files, params, context.paths)
        job = context.jobs.submit(
            "upload",
            lambda: upload_files(
                payload.files,
                params,
                context.paths,
                context.database,
                max_attempts=max(1, int(context.config.get("max_upload_limit", 8) or 8)),
            ),
            idempotency_key=idempotency_key,
        )
    except JobCapacityError as exc:
        raise HTTPException(429, str(exc)) from exc
    except JobAdmissionClosedError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"accepted": True, "task": job.id}


@router.get("/v1/uploads/{job_id}")
def upload_status(job_id: str, context: AppContext = Depends(get_context)) -> dict:
    job = context.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "upload job not found")
    return job.as_dict()
