from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

import biliup.integrations.uploaders
import biliup.platforms
from biliup.common.util import client
from biliup.config import ConfigStore
from biliup.core import AppPaths
from biliup.database.models import FileItem, LiveStreamer, StreamerInfo, UploadStreamer
from biliup.database.session import Database
from biliup.engine import Plugin
from biliup.integrations.uploader import upload_files

from .hooks import HookRunner
from .recorder import FFmpegRecorder, RecorderSpec

logger = logging.getLogger("biliup.scheduler")


def recording_allowed(
    title: str | None,
    excluded_keywords: list[str] | None,
    time_range: str | list[str] | None,
    *,
    now: datetime | None = None,
) -> bool:
    excluded = (keyword.strip() for keyword in excluded_keywords or [])
    if title and any(keyword in title for keyword in excluded if keyword):
        return False
    if not time_range:
        return True
    try:
        values = json.loads(time_range) if isinstance(time_range, str) else time_range
        if not isinstance(values, list) or len(values) != 2:
            return True
        start_value = datetime.fromisoformat(values[0].replace("Z", "+00:00"))
        end_value = datetime.fromisoformat(values[1].replace("Z", "+00:00"))
        if start_value.tzinfo is not None:
            start_value = start_value.astimezone(timezone.utc).replace(tzinfo=None)
        if end_value.tzinfo is not None:
            end_value = end_value.astimezone(timezone.utc).replace(tzinfo=None)
        start = start_value.time()
        end = end_value.time()
    except (TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Ignoring invalid recording time range: %r", time_range)
        return True
    current_value = now or datetime.now(timezone.utc)
    if current_value.tzinfo is not None:
        current_value = current_value.astimezone(timezone.utc).replace(tzinfo=None)
    current = current_value.time()
    return start <= current <= end if start <= end else current >= start or current <= end


@dataclass(slots=True)
class WorkerState:
    streamer_id: int
    status: str = "Pending"
    upload_status: str = "Idle"
    paused: bool = False
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    recorder: FFmpegRecorder | None = field(default=None, repr=False)


class RecordingScheduler:
    def __init__(self, database: Database, paths: AppPaths, config: ConfigStore, *, enabled: bool = True):
        self.database = database
        self.paths = paths
        self.config = config
        self.workers: dict[int, WorkerState] = {}
        self.hooks = HookRunner(paths)
        self.enabled = enabled
        self._closing = False
        self._plugins_loaded = False
        self.download_semaphore = asyncio.Semaphore(max(1, int(config.get("pool1_size", 5) or 5)))
        self.upload_semaphore = asyncio.Semaphore(max(1, int(config.get("pool2_size", 3) or 3)))

    async def start(self) -> None:
        if not self.enabled:
            return
        if not self._plugins_loaded:
            Plugin(biliup.platforms)
            Plugin(biliup.integrations.uploaders)
            self._plugins_loaded = True
        await self.reload()

    async def stop(self) -> None:
        self._closing = True
        states = list(self.workers.values())
        for state in states:
            if state.recorder:
                await state.recorder.stop()
            if state.task:
                state.task.cancel()
        await asyncio.gather(*(state.task for state in states if state.task), return_exceptions=True)
        self.workers.clear()

    async def reload(self) -> None:
        if not self.enabled:
            return
        with self.database.session_factory() as session:
            ids = set(session.scalars(select(LiveStreamer.id)).all())
        for streamer_id in set(self.workers) - ids:
            await self.remove(streamer_id)
        for streamer_id in ids - set(self.workers):
            state = WorkerState(streamer_id)
            state.task = asyncio.create_task(self._monitor(state), name=f"streamer-{streamer_id}")
            self.workers[streamer_id] = state

    async def remove(self, streamer_id: int) -> None:
        state = self.workers.pop(streamer_id, None)
        if not state:
            return
        if state.recorder:
            await state.recorder.stop()
        if state.task:
            state.task.cancel()
            await asyncio.gather(state.task, return_exceptions=True)

    async def toggle_pause(self, streamer_id: int) -> WorkerState:
        state = self.workers.get(streamer_id)
        if state is None:
            raise KeyError(streamer_id)
        state.paused = not state.paused
        state.status = "Paused" if state.paused else "Pending"
        if state.paused and state.recorder:
            await state.recorder.stop()
        return state

    def snapshot(self) -> dict[int, WorkerState]:
        return dict(self.workers)

    def _load_streamer(self, session: Session, streamer_id: int) -> LiveStreamer | None:
        return session.scalar(select(LiveStreamer).where(LiveStreamer.id == streamer_id))

    async def _monitor(self, state: WorkerState) -> None:
        while not self._closing:
            checker = None
            try:
                if state.paused:
                    await asyncio.sleep(1)
                    continue
                with self.database.session_factory() as session:
                    streamer = self._load_streamer(session, state.streamer_id)
                    if streamer is None:
                        return
                    payload = self._streamer_payload(streamer)
                state.status = "Checking"
                checker_type = Plugin.inspect_checker(payload["url"])
                with self.config.overlay(payload["override"]):
                    checker = checker_type(payload["remark"], payload["url"], payload["format"] or "flv")
                is_live = await checker.acheck_stream(is_check=False)
                if is_live and recording_allowed(
                    checker.room_title,
                    payload["excluded_keywords"],
                    payload["time_range"],
                ):
                    await self._record(state, payload, checker)
                else:
                    state.status = "Idle"
                    state.error = None
                    await asyncio.sleep(float(self.config.get("event_loop_interval", 30)))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state.status = "Error"
                state.error = str(exc)
                logger.exception("Streamer %s monitor failed", state.streamer_id)
                await asyncio.sleep(float(self.config.get("event_loop_interval", 30)))
            finally:
                if checker is not None and hasattr(checker, "close"):
                    await asyncio.to_thread(checker.close)

    @staticmethod
    def _streamer_payload(streamer: LiveStreamer) -> dict[str, Any]:
        return {
            "id": streamer.id,
            "url": streamer.url,
            "remark": streamer.remark,
            "filename_prefix": streamer.filename_prefix,
            "time_range": streamer.time_range,
            "excluded_keywords": streamer.excluded_keywords,
            "format": streamer.format,
            "override": streamer.override or {},
            "preprocessor": streamer.preprocessor,
            "segment_processor": streamer.segment_processor,
            "downloaded_processor": streamer.downloaded_processor,
            "postprocessor": streamer.postprocessor,
            "opt_args": streamer.opt_args,
            "upload_streamers_id": streamer.upload_streamers_id,
        }

    async def _record(self, state: WorkerState, payload: dict[str, Any], checker: Any) -> None:
        state.status = "Waiting"
        async with self.download_semaphore:
            if state.paused or self._closing:
                return
            await self._record_active(state, payload, checker)

    async def _record_active(self, state: WorkerState, payload: dict[str, Any], checker: Any) -> None:
        now = datetime.now()
        title = checker.room_title or payload["remark"]
        context = {
            "name": payload["remark"],
            "url": payload["url"],
            "room_title": title,
            "start_time": int(now.timestamp()),
        }
        await self.hooks.run_commands(payload["preprocessor"], context)
        override = payload["override"]
        if not checker.raw_stream_url:
            raise RuntimeError(f"{checker.__class__.__name__} did not provide a recordable stream URL")
        spec = RecorderSpec(
            name=payload["remark"],
            url=payload["url"],
            title=title,
            stream_url=checker.raw_stream_url,
            headers=dict(checker.stream_headers),
            output_dir=self.paths.downloads,
            format=payload["format"] or override.get("format") or "flv",
            segment_time=override.get("segment_time", self.config.get("segment_time")),
            file_size=override.get("file_size", self.config.get("file_size")),
            filename_prefix=payload["filename_prefix"] or self.config.get("filename_prefix"),
            extra_args=payload["opt_args"] or [],
        )
        state.status = "Downloading"
        state.error = None
        state.recorder = FFmpegRecorder(spec)
        stem = state.recorder.prepare_stem()
        cover_path = await self._download_cover(checker, payload, stem)
        context["live_cover_path"] = str(cover_path) if cover_path else ""
        with self.database.session_factory.begin() as session:
            info = StreamerInfo(
                name=payload["remark"],
                url=payload["url"],
                title=title,
                date=now,
                live_cover_path=context["live_cover_path"],
            )
            session.add(info)
            session.flush()
            info_id = info.id
        files: list[Path] = []
        segment_tasks: list[asyncio.Task[None]] = []
        threshold = int(override.get("filtering_threshold", self.config.get("filtering_threshold", 20)) or 0)
        parallel = bool(
            override.get("segment_processor_parallel", self.config.get("segment_processor_parallel", False))
        )

        try:
            checker.danmaku_init(str(self.paths.downloads / stem))
            if checker.danmaku:
                await asyncio.to_thread(checker.danmaku.start)
        except Exception:
            logger.exception("Danmaku initialization failed for %s", payload["remark"])

        async def segment_ready(file: Path) -> None:
            if checker.danmaku:
                await asyncio.to_thread(checker.danmaku.save, str(file.with_suffix(".xml")))
            if threshold and file.stat().st_size < threshold * 1024 * 1024:
                logger.info("Discarding recording fragment below %s MiB: %s", threshold, file)
                file.unlink(missing_ok=True)
                file.with_suffix(".xml").unlink(missing_ok=True)
                return
            files.append(file)
            operation = self.hooks.run_commands(payload["segment_processor"], {**context, "file": str(file)})
            if parallel:
                segment_tasks.append(asyncio.create_task(operation, name=f"segment-hook-{info_id}"))
            else:
                await operation

        try:
            await state.recorder.run(segment_ready)
        finally:
            state.recorder = None
            if checker.danmaku:
                await asyncio.to_thread(checker.danmaku.stop)
            if segment_tasks:
                await asyncio.gather(*segment_tasks)
        with self.database.session_factory.begin() as session:
            session.add_all(FileItem(file=str(file), streamer_info_id=info_id) for file in files)
        context.update({"end_time": int(datetime.now().timestamp()), "file_list": [str(file) for file in files]})
        await self.hooks.run_commands(payload["downloaded_processor"], context)
        upload_succeeded: bool | None = None
        if payload["upload_streamers_id"] and files:
            upload_succeeded = await self._upload(state, payload, context, files)
        if files and (upload_succeeded is True or (upload_succeeded is None and payload["postprocessor"])):
            steps = payload["postprocessor"]
            if upload_succeeded is True and steps is None:
                steps = ["rm"]
            await self.hooks.run_postprocessors(steps, files, context)
        state.status = "Idle"

    async def _download_cover(self, checker: Any, payload: dict[str, Any], stem: str) -> Path | None:
        enabled = payload["override"].get("use_live_cover", self.config.get("use_live_cover", False))
        url = getattr(checker, "live_cover_url", None)
        if not enabled or not url:
            return None
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            suffix = ".jpg"
        directory = self.paths.cache / "covers" / str(payload["id"])
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{stem}{suffix}"
        response = await client.get(url, headers=dict(checker.stream_headers), timeout=30)
        response.raise_for_status()
        await asyncio.to_thread(target.write_bytes, response.content)
        if suffix == ".webp":
            converted = target.with_suffix(".jpg")
            await asyncio.to_thread(self._convert_cover, target, converted)
            target.unlink(missing_ok=True)
            target = converted
        return target

    @staticmethod
    def _convert_cover(source: Path, target: Path) -> None:
        with Image.open(source) as image:
            image.convert("RGB").save(target, format="JPEG")

    async def _upload(
        self,
        state: WorkerState,
        payload: dict[str, Any],
        context: dict[str, Any],
        files: list,
    ) -> bool:
        with self.database.session_factory() as session:
            template = session.get(UploadStreamer, payload["upload_streamers_id"])
            if template is None:
                state.upload_status = "Error"
                state.error = "upload template not found"
                return False
            params = {column.name: getattr(template, column.name) for column in template.__table__.columns}
        for key in ("submit_api", "lines", "threads"):
            params[key] = payload["override"].get(key, self.config.get(key))
        params["user"] = payload["override"].get("user", self.config.get("user", {}))
        params["source_url"] = context["url"]
        delay = int(self.config.get("delay", 0) or 0)
        if delay:
            state.upload_status = "Waiting"
            await asyncio.sleep(delay)
        params["title"] = self._format_text(params.get("title") or context["room_title"], context)
        params["description"] = self._format_text(params.get("description") or "", context)
        if not params.get("cover_path") and context.get("live_cover_path"):
            params["cover_path"] = context["live_cover_path"]
        limit = max(1, int(self.config.get("max_upload_limit", 8) or 8))
        for attempt in range(1, limit + 1):
            state.upload_status = "Uploading"
            try:
                async with self.upload_semaphore:
                    await upload_files([str(file) for file in files], params, self.paths)
                state.upload_status = "Idle"
                state.error = None
                return True
            except Exception as exc:
                state.upload_status = "Error"
                state.error = str(exc)
                logger.exception("Upload attempt %s/%s failed for streamer %s", attempt, limit, state.streamer_id)
                if attempt < limit:
                    await asyncio.sleep(min(2**attempt, 30))
        return False

    @staticmethod
    def _format_text(template: str, context: dict[str, Any]) -> str:
        formatted = template.format(
            streamer=context["name"],
            title=context["room_title"],
            url=context["url"],
        )
        return time.strftime(formatted, time.localtime(context["start_time"]))
