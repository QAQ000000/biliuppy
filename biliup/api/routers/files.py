from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from biliup import __version__
from biliup.database.models import FileItem, LiveStreamer, StreamerInfo

from ..context import AppContext
from ..dependencies import get_context, get_session
from ..schemas import orm_dict, streamer_info_dict

router = APIRouter()
MEDIA_EXTENSIONS = {".mp4", ".flv", ".3gp", ".webm", ".mkv", ".ts"}


@router.get("/v1/videos")
def list_videos(context: AppContext = Depends(get_context)) -> list[dict]:
    result = []
    for index, path in enumerate(sorted(context.paths.downloads.iterdir()), start=1):
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        stat = path.stat()
        result.append({"key": index, "name": path.name, "updateTime": int(stat.st_mtime), "size": stat.st_size})
    return result


@router.get("/v1/streamer-info")
def list_streamer_info(session: Session = Depends(get_session)) -> list[dict]:
    return [streamer_info_dict(row) for row in session.scalars(select(StreamerInfo).order_by(StreamerInfo.id.desc()))]


@router.get("/v1/streamer-info/files/{info_id}")
def list_streamer_files(info_id: int, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(FileItem).where(FileItem.streamer_info_id == info_id)).all()
    return [orm_dict(row) for row in rows]


@router.get("/v1/status")
def status(context: AppContext = Depends(get_context), session: Session = Depends(get_session)) -> dict:
    rooms = []
    for streamer_id, worker in context.scheduler.snapshot().items():
        streamer = session.get(LiveStreamer, streamer_id)
        if streamer:
            rooms.append(
                {
                    "downloader_status": worker.status,
                    "uploader_status": worker.upload_status,
                    "live_streamer": orm_dict(streamer),
                    "upload_streamer": None,
                }
            )
    return {
        "version": __version__,
        "rooms": rooms,
        "download_semaphore": sum(room["downloader_status"] == "Downloading" for room in rooms),
        "update_semaphore": sum(room["uploader_status"] == "Uploading" for room in rooms),
        "config": context.config.snapshot(),
    }
