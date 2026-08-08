from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

from biliup.config import ConfigStore
from biliup.core import AppPaths
from biliup.database import Database
from biliup.database.models import FileItem, PendingSubmission, StreamerInfo
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
