from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from biliup.config import ConfigStore
from biliup.core import AppPaths
from biliup.database import Database
from biliup.database.models import FileItem, PendingSubmission, StreamerInfo
from biliup.integrations.uploader import (
    register_active_uploads,
    release_media_paths,
    reserve_media_paths,
    unregister_active_uploads,
)
from biliup.services.media_storage import MediaStorageService


def make_service(
    tmp_path: Path,
    config: dict | None = None,
    *,
    protected_paths=None,
) -> tuple[MediaStorageService, Database, AppPaths]:
    paths = AppPaths.discover(tmp_path).ensure()
    database = Database(paths.database)
    database.migrate()
    service = MediaStorageService(
        database,
        paths,
        ConfigStore(config or {}),
        protected_paths=protected_paths,
    )
    return service, database, paths


def test_retention_deletes_expired_media_but_keeps_parts_and_active_uploads(tmp_path: Path) -> None:
    protected: set[Path] = set()
    service, database, paths = make_service(
        tmp_path,
        {"recording_retention_days": 3},
        protected_paths=lambda: protected,
    )
    tracked = paths.downloads / "tracked.flv"
    orphan = paths.downloads / "orphan.flv"
    part = paths.downloads / "interrupted.part.flv"
    active = paths.downloads / "uploading.flv"
    pending_review = paths.downloads / "pending-review.flv"
    for path in (tracked, orphan, part, active, pending_review):
        path.write_bytes(path.name.encode("ascii"))
        os.utime(path, (time.time() - 4 * 86400, time.time() - 4 * 86400))
    protected.add(active.resolve())
    with database.session_factory.begin() as session:
        info = StreamerInfo(
            name="demo",
            url="https://example.invalid/live",
            title="demo",
            date=datetime.now(),
            live_cover_path="",
        )
        session.add(info)
        session.flush()
        session.add(FileItem(file=str(tracked.resolve()), streamer_info_id=info.id))
        now = datetime.now()
        session.add(
            PendingSubmission(
                aid=1,
                bvid="BV1",
                account_key="mid:1",
                cookie_path="cookies.json",
                source_files=[str(pending_review.resolve())],
                status="pending",
                archive_state=None,
                state_description=None,
                last_error=None,
                created_at=now,
                checked_at=None,
                updated_at=now,
            )
        )

    result = service.cleanup_expired()

    assert result["deleted_files"] == 2
    assert not tracked.exists()
    assert not orphan.exists()
    assert part.exists()
    assert active.exists()
    assert pending_review.exists()
    with database.session_factory() as session:
        assert session.query(FileItem).count() == 0
    database.dispose()


def test_media_reservation_and_upload_registration_are_mutually_exclusive(tmp_path: Path) -> None:
    media = tmp_path / "video.flv"
    media.write_bytes(b"video")

    registered = register_active_uploads([media])
    try:
        assert reserve_media_paths([media]) is None
    finally:
        unregister_active_uploads(registered)

    reserved = reserve_media_paths([media])
    assert reserved is not None
    try:
        with pytest.raises(ValueError, match="being recovered or deleted"):
            register_active_uploads([media])
    finally:
        release_media_paths(reserved)


def test_sidecar_failure_does_not_hide_deleted_video_or_leave_file_item(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, database, paths = make_service(tmp_path, {"recording_retention_days": 1})
    video = paths.downloads / "old.flv"
    sidecar = paths.downloads / "old.xml"
    video.write_bytes(b"video")
    sidecar.write_text("danmaku", encoding="utf-8")
    old = time.time() - 2 * 86400
    os.utime(video, (old, old))
    with database.session_factory.begin() as session:
        info = StreamerInfo(
            name="demo",
            url="https://example.invalid/live",
            title="demo",
            date=datetime.now(),
            live_cover_path="",
        )
        session.add(info)
        session.flush()
        session.add(FileItem(file=str(video.resolve()), streamer_info_id=info.id))

    original_unlink = Path.unlink

    def fail_sidecar(path: Path, *args, **kwargs):
        if path == sidecar:
            raise PermissionError("sidecar is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_sidecar)

    result = service.cleanup_expired()

    assert result == {"deleted_files": 1, "deleted_bytes": 5}
    assert not video.exists()
    assert sidecar.exists()
    with database.session_factory() as session:
        assert session.query(FileItem).count() == 0
    database.dispose()


async def test_orphan_part_can_be_recovered_without_touching_managed_media(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, database, paths = make_service(tmp_path)
    managed = paths.downloads / "managed.flv"
    part = paths.downloads / "interrupted.part.flv"
    legacy_part = paths.downloads / "legacy.flv.part"
    managed.write_bytes(b"managed")
    part.write_bytes(b"partial")
    legacy_part.write_bytes(b"legacy partial")
    with database.session_factory.begin() as session:
        info = StreamerInfo(
            name="demo",
            url="https://example.invalid/live",
            title="demo",
            date=datetime.now(),
            live_cover_path="",
        )
        session.add(info)
        session.flush()
        session.add(FileItem(file=str(managed.resolve()), streamer_info_id=info.id))

    async def fake_remux(source: Path, target: Path) -> None:
        source.replace(target)

    monkeypatch.setattr(MediaStorageService, "_remux_part", staticmethod(fake_remux))

    unmanaged = service.list_unmanaged()
    assert {item["name"] for item in unmanaged} == {part.name, legacy_part.name}
    recovered = await service.recover_parts([part.name, legacy_part.name])

    assert recovered == [
        {"source": part.name, "file": "interrupted.flv"},
        {"source": legacy_part.name, "file": "legacy.flv"},
    ]
    assert not part.exists()
    assert not legacy_part.exists()
    assert (paths.downloads / "interrupted.flv").read_bytes() == b"partial"
    assert (paths.downloads / "legacy.flv").read_bytes() == b"legacy partial"
    assert managed.read_bytes() == b"managed"
    database.dispose()


async def test_orphan_part_recovery_rejects_a_concurrent_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, database, paths = make_service(tmp_path)
    part = paths.downloads / "interrupted.part.flv"
    part.write_bytes(b"partial")
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_remux(source: Path, target: Path) -> None:
        started.set()
        await release.wait()
        source.replace(target)

    monkeypatch.setattr(MediaStorageService, "_remux_part", staticmethod(blocked_remux))
    first = asyncio.create_task(service.recover_parts([part.name]))
    await started.wait()

    with pytest.raises(ValueError, match="orphaned work files|currently in use"):
        await service.recover_parts([part.name])

    release.set()
    assert await first == [{"source": part.name, "file": "interrupted.flv"}]
    database.dispose()
