from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from biliup.database.models import FileItem, LiveStreamer, UploadStreamer
from biliup.integrations.uploader import (
    register_active_uploads,
    resolve_upload_files,
    unregister_active_uploads,
    upload_files,
    upload_idempotency_key,
)
from biliup.services import JobAdmissionClosedError, JobCapacityError
from biliup.services.upload_templates import render_upload_text

from ..context import AppContext
from ..dependencies import get_context, get_session
from ..schemas import ManualUploadInput, UploadStreamerInput, orm_dict

router = APIRouter()


_RECORDING_NAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<hour>\d{2})[_-](?P<minute>\d{2})[_-](?P<second>\d{2})(?P<title>.*)$"
)


def _parse_recording_name(stem: str, streamer_name: str) -> tuple[str, int] | None:
    without_part = re.sub(r"_\d{3,}$", "", stem)
    if not without_part.startswith(streamer_name):
        return None
    match = _RECORDING_NAME.match(without_part[len(streamer_name) :].lstrip(" _-"))
    if match is None:
        return None
    try:
        started_at = datetime.strptime(
            f"{match['date']} {match['hour']}:{match['minute']}:{match['second']}",
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        return None
    title = match["title"].strip(" _-") or without_part
    return title, int(started_at.timestamp())


def _manual_template_context(files: list[str], params: dict, context: AppContext) -> dict:
    resolved = resolve_upload_files(files, context.paths)
    first = resolved[0]
    template_context = {
        "name": first.stem,
        "room_title": first.stem,
        "url": "",
        "start_time": int(first.stat().st_mtime),
        "metadata_source": "file",
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
                    "metadata_source": "history",
                }
            )
            return template_context

        template_id = params.get("id")
        if template_id is not None:
            linked_streamers = list(
                session.scalars(
                    select(LiveStreamer)
                    .where(LiveStreamer.upload_streamers_id == int(template_id))
                    .order_by(LiveStreamer.id)
                )
            )
            matching = [streamer for streamer in linked_streamers if first.stem.startswith(streamer.remark)]
            linked = max(matching, key=lambda streamer: len(streamer.remark), default=None)
            if linked is None and len(linked_streamers) == 1:
                linked = linked_streamers[0]
            if linked is not None:
                template_context.update(
                    {
                        "name": linked.remark,
                        "url": linked.url,
                        "metadata_source": "filename",
                    }
                )
                parsed = _parse_recording_name(first.stem, linked.remark)
                if parsed is not None:
                    template_context["room_title"], template_context["start_time"] = parsed
    return template_context


def _prepare_manual_upload(payload: ManualUploadInput, context: AppContext) -> tuple[dict, dict]:
    params = payload.params.model_dump()
    for key in ("submit_api", "lines", "threads", "submit_interval"):
        params[key] = context.config.get(key)
    params["user"] = context.config.get("user", {})
    template_context = _manual_template_context(payload.files, params, context)
    params["title"] = render_upload_text(
        params.get("title") or template_context["room_title"],
        template_context,
    )[:80]
    params["description"] = render_upload_text(
        params.get("description") or "",
        template_context,
    )[:2000]
    params["dynamic"] = render_upload_text(
        params.get("dynamic") or "",
        template_context,
    )
    params["source_url"] = template_context["url"]
    return params, template_context


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
    try:
        params, _template_context = _prepare_manual_upload(payload, context)
        idempotency_key = upload_idempotency_key(payload.files, params, context.paths)
        job = context.jobs.get_active("upload", idempotency_key)
        if job is None:
            registered = register_active_uploads(resolve_upload_files(payload.files, context.paths))
            released = False

            def release_paths() -> None:
                nonlocal released
                if not released:
                    released = True
                    unregister_active_uploads(registered)

            async def run_upload():
                try:
                    return await upload_files(
                        payload.files,
                        params,
                        context.paths,
                        context.database,
                        max_attempts=max(1, int(context.config.get("max_upload_limit", 8) or 8)),
                    )
                finally:
                    release_paths()

            try:
                job = context.jobs.submit(
                    "upload",
                    run_upload,
                    idempotency_key=idempotency_key,
                )
            except Exception:
                release_paths()
                raise
            task = context.jobs.tasks.get(job.id)
            if task is not None:
                task.add_done_callback(lambda _completed: release_paths())
    except JobCapacityError as exc:
        raise HTTPException(429, str(exc)) from exc
    except JobAdmissionClosedError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"accepted": True, "task": job.id}


@router.post("/v1/uploads/preview")
def manual_upload_preview(payload: ManualUploadInput, context: AppContext = Depends(get_context)) -> dict:
    try:
        params, template_context = _prepare_manual_upload(payload, context)
        resolved = resolve_upload_files(payload.files, context.paths)
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "title": params["title"],
        "description": params["description"],
        "dynamic": params["dynamic"],
        "streamer": template_context["name"],
        "room_title": template_context["room_title"],
        "url": template_context["url"],
        "start_time": datetime.fromtimestamp(template_context["start_time"]).isoformat(),
        "metadata_source": template_context["metadata_source"],
        "parts": [{"file": path.name, "title": path.stem[:80]} for path in resolved],
    }


@router.get("/v1/uploads/{job_id}")
def upload_status(job_id: str, context: AppContext = Depends(get_context)) -> dict:
    job = context.jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "upload job not found")
    return job.as_dict()
