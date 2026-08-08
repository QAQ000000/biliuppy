from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from sqlalchemy import delete, select

from biliup.config import ConfigStore
from biliup.core import AppPaths
from biliup.core.media import (
    completed_path_for_work_file,
    is_media_file,
    is_recording_work_file,
    media_sidecar_path,
)
from biliup.database.models import FileItem, PendingSubmission
from biliup.database.session import Database
from biliup.integrations.uploader import (
    active_upload_paths,
    busy_media_paths,
    release_media_paths,
    reserve_media_paths,
)

logger = logging.getLogger("biliup.media_storage")


class MediaStorageService:
    CLEANUP_INTERVAL_SECONDS = 60 * 60

    def __init__(
        self,
        database: Database,
        paths: AppPaths,
        config: ConfigStore,
        *,
        protected_paths: Callable[[], set[Path]] | None = None,
    ) -> None:
        self.database = database
        self.paths = paths
        self.config = config
        self.protected_paths = protected_paths or set
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        unmanaged = self.list_unmanaged()
        part_count = sum(item["kind"] == "part" for item in unmanaged)
        if part_count:
            logger.warning("Detected %s orphaned recording work file(s)", part_count)
        await asyncio.to_thread(self.cleanup_expired)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="media-retention")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    def _protected(self) -> set[Path]:
        return {
            path.resolve()
            for path in (
                *self.protected_paths(),
                *busy_media_paths(),
                *self._pending_submission_paths(),
            )
        }

    def _persistent_protected(self) -> set[Path]:
        return {
            path.resolve()
            for path in (
                *self.protected_paths(),
                *active_upload_paths(),
                *self._pending_submission_paths(),
            )
        }

    def _pending_submission_paths(self) -> set[Path]:
        with self.database.session_factory() as session:
            source_files = session.scalars(
                select(PendingSubmission.source_files).where(PendingSubmission.status == "pending")
            )
            return {
                Path(value).resolve()
                for values in source_files
                for value in values
            }

    def _tracked(self) -> set[Path]:
        with self.database.session_factory() as session:
            tracked = {
                Path(value).resolve()
                for value in session.scalars(select(FileItem.file))
            }
            for source_files in session.scalars(select(PendingSubmission.source_files)):
                tracked.update(Path(value).resolve() for value in source_files)
        return tracked

    def _media_files(self) -> list[Path]:
        root = self.paths.downloads.resolve()
        return [
            path
            for path in self.paths.downloads.iterdir()
            if path.is_file() and path.resolve().parent == root and is_media_file(path)
        ]

    @staticmethod
    def _item(path: Path) -> dict[str, int | str | bool]:
        stat = path.stat()
        work_file = is_recording_work_file(path)
        return {
            "name": path.name,
            "size": stat.st_size,
            "updateTime": int(stat.st_mtime),
            "kind": "part" if work_file else "media",
            "recoverable": work_file,
        }

    def list_unmanaged(self) -> list[dict[str, int | str | bool]]:
        tracked = self._tracked()
        protected = self._protected()
        result = []
        for path in self._media_files():
            try:
                resolved = path.resolve()
                if resolved in protected:
                    continue
                if is_recording_work_file(path) or resolved not in tracked:
                    result.append(self._item(path))
            except FileNotFoundError:
                continue
        return sorted(result, key=lambda item: (int(item["updateTime"]), str(item["name"])), reverse=True)

    def cleanup_expired(self) -> dict[str, int]:
        retention_days = max(0, int(self.config.get("recording_retention_days", 0) or 0))
        if not retention_days:
            return {"deleted_files": 0, "deleted_bytes": 0}
        cutoff = time.time() - retention_days * 24 * 60 * 60
        protected = self._persistent_protected()
        deleted_paths: list[str] = []
        deleted_bytes = 0
        for path in self._media_files():
            if is_recording_work_file(path):
                continue
            reserved = reserve_media_paths([path])
            if reserved is None:
                continue
            try:
                if path.resolve() in protected:
                    continue
                try:
                    if path.stat().st_mtime >= cutoff:
                        continue
                except FileNotFoundError:
                    continue
                removed, removed_bytes = self._unlink_paths([path])
                deleted_paths.extend(removed)
                deleted_bytes += removed_bytes
            finally:
                release_media_paths(reserved)
        self._remove_file_items(deleted_paths)
        result = {"deleted_files": len(deleted_paths), "deleted_bytes": deleted_bytes}
        if result["deleted_files"]:
            logger.info(
                "Recording retention removed %s file(s), %s byte(s), older_than_days=%s",
                result["deleted_files"],
                result["deleted_bytes"],
                retention_days,
            )
        return result

    def delete_unmanaged(self, names: Iterable[str]) -> dict[str, int]:
        requested = set(names)
        available = {str(item["name"]): item for item in self.list_unmanaged()}
        missing = sorted(requested - available.keys())
        if missing:
            raise ValueError(f"Files are not unmanaged or are currently in use: {', '.join(missing)}")
        paths = [self._resolve_name(name) for name in requested]
        reserved = reserve_media_paths(paths)
        if reserved is None:
            raise ValueError("One or more files are currently in use")
        try:
            protected = self._persistent_protected()
            tracked = self._tracked()
            invalid = [
                path.name
                for path in paths
                if path.resolve() in protected
                or (not is_recording_work_file(path) and path.resolve() in tracked)
            ]
            if invalid:
                raise ValueError(f"Files are not unmanaged or are currently in use: {', '.join(invalid)}")
            deleted_paths, deleted_bytes = self._unlink_paths(paths)
            self._remove_file_items(deleted_paths)
            return {"deleted_files": len(deleted_paths), "deleted_bytes": deleted_bytes}
        finally:
            release_media_paths(reserved)

    async def recover_parts(self, names: Iterable[str]) -> list[dict[str, str]]:
        requested = list(dict.fromkeys(names))
        available = {
            str(item["name"])
            for item in self.list_unmanaged()
            if item["kind"] == "part"
        }
        missing = sorted(set(requested) - available)
        if missing:
            raise ValueError(f"Files are not orphaned work files or are currently in use: {', '.join(missing)}")
        sources = [self._resolve_name(name) for name in requested]
        targets = [completed_path_for_work_file(source) for source in sources]
        temporary_paths = [self._recovery_temporary_path(target) for target in targets]
        reserved = reserve_media_paths([*sources, *targets, *temporary_paths])
        if reserved is None:
            raise ValueError("One or more files are currently in use")
        try:
            protected = self._persistent_protected()
            recovered = []
            for source, target in zip(sources, targets, strict=True):
                if source.resolve() in protected or not source.is_file():
                    raise ValueError(f"File is no longer an orphaned work file: {source.name}")
                if target.exists():
                    raise FileExistsError(f"Recovery target already exists: {target.name}")
            for source, target in zip(sources, targets, strict=True):
                await self._remux_part(source, target)
                recovered.append({"source": source.name, "file": target.name})
                logger.info("Recovered orphaned recording file %s as %s", source.name, target.name)
            return recovered
        finally:
            release_media_paths(reserved)

    def _resolve_name(self, name: str) -> Path:
        if not name or Path(name).name != name:
            raise ValueError(f"Invalid media filename: {name}")
        path = self.paths.downloads / name
        if not path.is_file() or path.resolve().parent != self.paths.downloads.resolve():
            raise FileNotFoundError(name)
        return path

    def _unlink_paths(self, paths: Iterable[Path]) -> tuple[list[str], int]:
        deleted_paths: list[str] = []
        deleted_bytes = 0
        for path in paths:
            try:
                size = path.stat().st_size
                resolved = str(path.resolve())
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                logger.warning("Failed to delete retained media file %s: %s", path, exc)
                continue
            deleted_paths.append(resolved)
            deleted_bytes += size
            try:
                media_sidecar_path(path).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Failed to delete media sidecar %s: %s", media_sidecar_path(path), exc)
        return deleted_paths, deleted_bytes

    def _remove_file_items(self, deleted_paths: list[str]) -> None:
        if not deleted_paths:
            return

        def remove_file_items(session) -> None:
            session.execute(delete(FileItem).where(FileItem.file.in_(deleted_paths)))

        self.database.run_write(remove_file_items)

    @staticmethod
    def _recovery_temporary_path(target: Path) -> Path:
        return target.with_name(f"{target.stem}.recovering{target.suffix}")

    @staticmethod
    async def _remux_part(source: Path, target: Path) -> None:
        temporary = MediaStorageService._recovery_temporary_path(target)
        temporary.unlink(missing_ok=True)
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0",
                "-c",
                "copy",
                str(temporary),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("FFmpeg is not installed or is not available on PATH") from exc
        _stdout, stderr = await process.communicate()
        if process.returncode or not temporary.is_file() or not temporary.stat().st_size:
            temporary.unlink(missing_ok=True)
            message = stderr.decode(errors="replace").strip()[-2000:]
            raise RuntimeError(
                f"FFmpeg could not recover {source.name}: {message or f'exit code {process.returncode}'}"
            )
        temporary.replace(target)
        source.unlink()
        source_xml = media_sidecar_path(source)
        target_xml = target.with_suffix(".xml")
        if source_xml != target_xml and source_xml.is_file():
            source_xml.replace(target_xml)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.CLEANUP_INTERVAL_SECONDS)
            try:
                await asyncio.to_thread(self.cleanup_expired)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Recording retention cleanup failed")
