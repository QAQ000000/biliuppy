from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from biliup.database.models import FileItem, StreamerInfo


def prune_history(session: Session, keep: int) -> tuple[int, int]:
    if keep < 0:
        raise ValueError("history retention count cannot be negative")
    stale = select(StreamerInfo.id).order_by(StreamerInfo.id.desc()).offset(keep).subquery("stale_streamer_info")
    stale_ids = select(stale.c.id)
    record_count = session.scalar(select(func.count()).select_from(stale)) or 0
    if not record_count:
        return 0, 0
    file_count = session.scalar(select(func.count(FileItem.id)).where(FileItem.streamer_info_id.in_(stale_ids))) or 0
    session.execute(delete(FileItem).where(FileItem.streamer_info_id.in_(stale_ids)))
    session.execute(delete(StreamerInfo).where(StreamerInfo.id.in_(stale_ids)))
    return record_count, file_count
