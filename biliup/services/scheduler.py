from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import shutil
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
from biliup.core.redaction import redact_sensitive_text
from biliup.database.models import FileItem, LiveStreamer, StreamerInfo, UploadStreamer
from biliup.database.session import Database
from biliup.engine import Plugin, StreamProbeResult, StreamStatus
from biliup.integrations.upload_errors import is_transient_upload_error
from biliup.integrations.upload_state import UploadResult
from biliup.integrations.uploader import (
    register_active_uploads,
    unregister_active_uploads,
    upload_files,
)
from biliup.platforms.bilibili import configure_bilibili_rooms

from .history import prune_history
from .hooks import HookRunner
from .recorder import FFmpegRecorder, RecorderError, RecorderSpec, RecorderStorageError
from .submission_review import SubmissionReviewService
from .upload_templates import render_upload_text

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
    monitor_error: str | None = None
    upload_error: str | None = None
    capture_active: bool = False
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    recording_task: asyncio.Task[None] | None = field(default=None, repr=False)
    upload_tasks: set[asyncio.Task[None]] = field(default_factory=set, repr=False)
    recorder: FFmpegRecorder | None = field(default=None, repr=False)
    recording_stem: str | None = field(default=None, repr=False)
    recording_format: str | None = field(default=None, repr=False)
    wake_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    transition_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    upload_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @property
    def error(self) -> str | None:
        return self.upload_error or self.monitor_error

    @error.setter
    def error(self, value: str | None) -> None:
        self.monitor_error = value


class RecordingScheduler:
    def __init__(self, database: Database, paths: AppPaths, config: ConfigStore, *, enabled: bool = True):
        self.database = database
        self.paths = paths
        self.config = config
        self.workers: dict[int, WorkerState] = {}
        self.hooks = HookRunner(paths, timeout=float(config.get("hook_timeout", 300) or 300))
        self.enabled = enabled
        self._closing = False
        self._plugins_loaded = False
        self._clock = time.monotonic
        self.download_semaphore = asyncio.Semaphore(max(1, int(config.get("pool1_size", 5) or 5)))
        self.upload_semaphore = asyncio.Semaphore(max(1, int(config.get("pool2_size", 3) or 3)))
        self.segment_semaphore = asyncio.Semaphore(max(1, int(config.get("segment_processor_concurrency", 4) or 4)))
        self.checker_semaphore = asyncio.Semaphore(max(1, int(config.get("checker_concurrency", 3) or 3)))
        self.submission_reviews = SubmissionReviewService(database, paths)

    async def start(self) -> None:
        await self.submission_reviews.start()
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
        logger.info("Stopping scheduler with %s worker(s)", len(states))
        recording_tasks = []
        monitor_tasks = []
        for state in states:
            if state.recording_task and not state.recording_task.done():
                state.recording_task.cancel()
                recording_tasks.append(state.recording_task)
            if state.task and not state.task.done():
                state.task.cancel()
                monitor_tasks.append(state.task)
            if state.recorder:
                await state.recorder.stop()
        await asyncio.gather(*recording_tasks, *monitor_tasks, return_exceptions=True)
        upload_tasks = [task for state in states for task in tuple(state.upload_tasks)]
        for task in upload_tasks:
            task.cancel()
        await asyncio.gather(*upload_tasks, return_exceptions=True)
        await self.submission_reviews.stop()
        logger.info("Scheduler stopped")
        self.workers.clear()

    async def reload(self) -> None:
        if not self.enabled:
            return
        with self.database.session_factory() as session:
            streamers = session.execute(select(LiveStreamer.id, LiveStreamer.url)).all()
            ids = {streamer_id for streamer_id, _url in streamers}
            configure_bilibili_rooms(url for _streamer_id, url in streamers)
        for streamer_id in set(self.workers) - ids:
            await self.remove(streamer_id)
        new_ids = sorted(ids - set(self.workers))
        interval = max(0.0, float(self.config.get("event_loop_interval", 30) or 30))
        checker_sleep = max(0.0, float(self.config.get("checker_sleep", 10) or 0))
        spacing = min(checker_sleep, interval / max(1, len(new_ids)))
        for index, streamer_id in enumerate(new_ids):
            state = WorkerState(streamer_id)
            state.task = asyncio.create_task(
                self._monitor(state, initial_delay=index * spacing),
                name=f"streamer-{streamer_id}",
            )
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
        upload_tasks = list(state.upload_tasks)
        for task in upload_tasks:
            task.cancel()
        await asyncio.gather(*upload_tasks, return_exceptions=True)

    async def set_paused(self, streamer_id: int, paused: bool) -> WorkerState:
        state = self.workers.get(streamer_id)
        if state is None:
            raise KeyError(streamer_id)
        async with state.transition_lock:
            return await self._set_paused_locked(state, paused)

    async def toggle_pause(self, streamer_id: int) -> WorkerState:
        state = self.workers.get(streamer_id)
        if state is None:
            raise KeyError(streamer_id)
        async with state.transition_lock:
            return await self._set_paused_locked(state, not state.paused)

    @staticmethod
    async def _set_paused_locked(state: WorkerState, paused: bool) -> WorkerState:
        if state.paused == paused:
            return state
        state.paused = paused
        state.status = "Paused" if paused else "Checking"
        if paused:
            if state.capture_active:
                if state.recorder:
                    await state.recorder.stop()
                if state.capture_active and state.recording_task and not state.recording_task.done():
                    state.recording_task.cancel()
                    await asyncio.gather(state.recording_task, return_exceptions=True)
            state.status = "Paused"
        else:
            state.monitor_error = None
            state.wake_event.set()
        return state

    def snapshot(self) -> dict[int, WorkerState]:
        return dict(self.workers)

    def active_recording_paths(self) -> set[Path]:
        patterns = [
            re.compile(
                rf"^{re.escape(state.recording_stem)}(?:_\d+)?(?:\.part)?\.{re.escape(state.recording_format)}$",
                re.IGNORECASE,
            )
            for state in list(self.workers.values())
            if state.recording_stem and state.recording_format
        ]
        if not patterns:
            return set()
        return {
            path.resolve()
            for path in self.paths.downloads.iterdir()
            if path.is_file() and any(pattern.fullmatch(path.name) for pattern in patterns)
        }

    def _load_streamer(self, session: Session, streamer_id: int) -> LiveStreamer | None:
        return session.scalar(select(LiveStreamer).where(LiveStreamer.id == streamer_id))

    async def _monitor(self, state: WorkerState, *, initial_delay: float = 0) -> None:
        if initial_delay:
            await self._sleep_until_next_check(state, initial_delay)
        unknown_failures = 0
        while not self._closing:
            checker = None
            try:
                if state.paused:
                    await self._sleep_until_next_check(state, 1)
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
                probe = await self._probe_stream(checker, is_check=False)
                if state.paused:
                    state.status = "Paused"
                    continue
                if probe.status is StreamStatus.LIVE and recording_allowed(
                    checker.room_title,
                    payload["excluded_keywords"],
                    payload["time_range"],
                ):
                    recording_task = asyncio.create_task(
                        self._record(state, payload, checker),
                        name=f"recording-{state.streamer_id}",
                    )
                    state.recording_task = recording_task
                    try:
                        await recording_task
                    except asyncio.CancelledError:
                        if not state.paused:
                            raise
                    finally:
                        if state.recording_task is recording_task:
                            state.recording_task = None
                        state.recording_stem = None
                        state.recording_format = None
                    if state.paused:
                        state.status = "Paused"
                    unknown_failures = 0
                elif probe.status is StreamStatus.UNRECORDABLE:
                    unknown_failures = 0
                    state.status = "Unrecordable"
                    state.monitor_error = probe.reason or "Live stream is not recordable"
                    await self._sleep_until_next_check(
                        state,
                        float(self.config.get("event_loop_interval", 30)),
                    )
                elif probe.status is StreamStatus.UNKNOWN:
                    unknown_failures += 1
                    state.status = "Degraded"
                    state.monitor_error = probe.reason or "Live status is temporarily unavailable"
                    base = max(1.0, float(self.config.get("event_loop_interval", 30) or 30))
                    ceiling = max(base, 300.0)
                    backoff = min(base * (2 ** min(unknown_failures - 1, 4)), ceiling)
                    jitter = random.uniform(0, max(0.0, float(self.config.get("checker_sleep", 10) or 0)))
                    await self._sleep_until_next_check(state, backoff + jitter)
                else:
                    unknown_failures = 0
                    state.status = "Idle"
                    state.monitor_error = None
                    await self._sleep_until_next_check(
                        state,
                        float(self.config.get("event_loop_interval", 30)),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state.status = "Error"
                state.monitor_error = str(exc)
                logger.exception("Streamer %s monitor failed", state.streamer_id)
                await self._sleep_until_next_check(
                    state,
                    float(self.config.get("event_loop_interval", 30)),
                )
            finally:
                if checker is not None and hasattr(checker, "close"):
                    await asyncio.to_thread(checker.close)

    @staticmethod
    async def _sleep_until_next_check(state: WorkerState, delay: float) -> None:
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(state.wake_event.wait(), timeout=delay)
        except TimeoutError:
            pass
        finally:
            state.wake_event.clear()

    async def _probe_stream(self, checker: Any, *, is_check: bool) -> StreamProbeResult:
        async with self.checker_semaphore:
            if hasattr(checker, "aprobe_stream"):
                try:
                    result = await checker.aprobe_stream(is_check=is_check)
                except Exception as exc:
                    return StreamProbeResult.unknown(str(exc))
                if isinstance(result, StreamProbeResult):
                    return result
                return StreamProbeResult.live() if result else StreamProbeResult.offline()
            try:
                is_live = await checker.acheck_stream(is_check=is_check)
            except Exception as exc:
                return StreamProbeResult.unknown(str(exc))
            return StreamProbeResult.live() if is_live else StreamProbeResult.offline()

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
        if state.paused or self._closing:
            return
        if error := self._disk_space_error():
            state.status = "Degraded"
            state.monitor_error = error
            logger.error("Recording blocked for %s: %s", payload["remark"], error)
            await asyncio.sleep(max(1.0, float(self.config.get("event_loop_interval", 30) or 30)))
            return
        await self._record_active(state, payload, checker)

    def _disk_space_error(self) -> str | None:
        required_gb = max(0, int(self.config.get("min_free_disk_gb", 5) or 0))
        if not required_gb:
            return None
        free_bytes = shutil.disk_usage(self.paths.downloads).free
        required_bytes = required_gb * 1024**3
        if free_bytes >= required_bytes:
            return None
        return f"Free disk space is {free_bytes / 1024**3:.2f} GB; at least {required_gb:g} GB is required"

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
            stall_timeout=self.config.get("recorder_stall_timeout", 90),
            min_free_bytes=max(0, int(self.config.get("min_free_disk_gb", 5) or 0)) * 1024**3,
        )
        files: list[Path] = []
        seen_files: set[Path] = set()
        recorder_failures = 0
        segment_tasks: set[asyncio.Task[None]] = set()
        segment_error: BaseException | None = None
        storage_error: RecorderStorageError | None = None
        threshold = int(override.get("filtering_threshold", self.config.get("filtering_threshold", 20)) or 0)
        parallel = bool(
            override.get("segment_processor_parallel", self.config.get("segment_processor_parallel", False))
        )

        async def run_segment_hook(file: Path) -> None:
            nonlocal segment_error
            try:
                await self.hooks.run_commands(payload["segment_processor"], {**context, "file": str(file)})
            except Exception as exc:
                segment_error = segment_error or exc
            finally:
                self.segment_semaphore.release()

        async def segment_ready(file: Path) -> None:
            if file in seen_files or not file.is_file():
                return
            seen_files.add(file)
            if checker.danmaku:
                await asyncio.to_thread(checker.danmaku.save, str(file.with_suffix(".xml")))
            if threshold and file.stat().st_size < threshold * 1024 * 1024:
                logger.info("Discarding recording fragment below %s MiB: %s", threshold, file)
                file.unlink(missing_ok=True)
                file.with_suffix(".xml").unlink(missing_ok=True)
                return
            files.append(file)
            if parallel:
                if segment_error:
                    raise segment_error
                await self.segment_semaphore.acquire()
                task = asyncio.create_task(
                    run_segment_hook(file),
                    name=f"segment-hook-{state.streamer_id}",
                )
                segment_tasks.add(task)
                task.add_done_callback(segment_tasks.discard)
            else:
                await self.hooks.run_commands(payload["segment_processor"], {**context, "file": str(file)})

        if state.paused or self._closing:
            state.status = "Paused" if state.paused else state.status
            return
        state.capture_active = True
        try:
            state.recorder = FFmpegRecorder(spec)
            stem = state.recorder.prepare_stem()
            state.recording_stem = stem
            state.recording_format = spec.format
            try:
                cover_path = await self._download_cover(checker, payload, stem)
            except Exception as exc:
                cover_path = None
                logger.warning(
                    "Live cover download failed for %s; recording will continue without it: %s",
                    payload["remark"],
                    redact_sensitive_text(str(exc)),
                )
            context["live_cover_path"] = str(cover_path) if cover_path else ""
            try:
                checker.danmaku_init(str(self.paths.downloads / stem))
                if checker.danmaku:
                    await asyncio.to_thread(checker.danmaku.start)
            except Exception:
                logger.exception("Danmaku initialization failed for %s", payload["remark"])
            try:
                while not state.paused and not self._closing:
                    recorder = state.recorder or FFmpegRecorder(spec)
                    state.recorder = recorder
                    recorder_error: RecorderError | None = None
                    run_started = self._clock()
                    try:
                        async with self.download_semaphore:
                            if state.paused or self._closing:
                                break
                            state.status = "Downloading"
                            state.monitor_error = None
                            await recorder.run(segment_ready)
                            if not state.paused and not self._closing:
                                recorder_error = RecorderError(
                                    "FFmpeg exited before the stream was confirmed offline"
                                )
                                if self._clock() - run_started >= 60:
                                    recorder_failures = 0
                                recorder_failures += 1
                                logger.warning(
                                    "Recorder for %s exited cleanly; checking live status",
                                    payload["remark"],
                                )
                    except RecorderStorageError as exc:
                        storage_error = exc
                        for file in recorder.output_files():
                            await segment_ready(file)
                        state.status = "Degraded"
                        state.monitor_error = str(exc)
                        logger.error("Recording stopped for %s: %s", payload["remark"], exc)
                    except RecorderError as exc:
                        recorder_error = exc
                        if self._clock() - run_started >= 60:
                            recorder_failures = 0
                        recorder_failures += 1
                        for file in recorder.output_files():
                            await segment_ready(file)
                        logger.warning(
                            "Recorder for %s stopped unexpectedly: %s; checking live status",
                            payload["remark"],
                            exc,
                        )
                    finally:
                        state.recorder = None

                    if state.paused or self._closing:
                        break
                    if storage_error:
                        break
                    if not await self._wait_for_stream_recovery(state, checker):
                        if recorder_error:
                            logger.info("Confirmed offline after recorder failure for %s", payload["remark"])
                        break
                    if recorder_error:
                        retry_limit = max(1, int(self.config.get("recorder_retry_limit", 10) or 10))
                        retry_base = max(1.0, float(self.config.get("recorder_retry_backoff", 5) or 5))
                        if recorder_failures >= retry_limit:
                            retry_delay = max(
                                300.0,
                                float(self.config.get("event_loop_interval", 30) or 30),
                            )
                            state.status = "Degraded"
                            state.monitor_error = (
                                f"Recorder failed {recorder_failures} consecutive times; "
                                f"retrying after a {retry_delay:g}-second cooldown"
                            )
                            logger.error(
                                "Recorder recovery circuit opened for %s after %s failures; cooling down %.1fs",
                                payload["remark"],
                                recorder_failures,
                                retry_delay,
                            )
                            recorder_failures = 0
                        else:
                            retry_delay = min(retry_base * (2 ** (recorder_failures - 1)), 60.0)
                            retry_delay += random.uniform(0, min(retry_base, 5.0))
                            state.status = "Recovering"
                            state.monitor_error = str(recorder_error)
                        await asyncio.sleep(retry_delay)
                        if state.paused or self._closing:
                            break
                        if not await self._wait_for_stream_recovery(state, checker):
                            break
                    else:
                        recorder_failures = 0
                    spec.stream_url = checker.raw_stream_url
                    spec.headers = dict(checker.stream_headers)
                    state.recorder = FFmpegRecorder(spec)
                    logger.info("Resuming recording with a refreshed stream URL for %s", payload["remark"])
            finally:
                if checker.danmaku:
                    await asyncio.to_thread(checker.danmaku.stop)
        except BaseException:
            for task in tuple(segment_tasks):
                task.cancel()
            await asyncio.gather(*segment_tasks, return_exceptions=True)
            raise
        finally:
            state.recorder = None
            state.capture_active = False

        while segment_tasks:
            await asyncio.gather(*tuple(segment_tasks), return_exceptions=True)
        if segment_error:
            raise segment_error
        if self._closing:
            return
        if not files:
            if error := self._disk_space_error():
                state.status = "Degraded"
                state.monitor_error = error
            else:
                state.status = "Paused" if state.paused else "Idle"
                state.monitor_error = None
            return

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
            session.add_all(FileItem(file=str(file), streamer_info_id=info.id) for file in files)
            removed_records, _ = prune_history(
                session,
                int(self.config.get("history_max_records", 10_000) or 10_000),
            )
            if removed_records:
                logger.info("Pruned %s old live history records", removed_records)
        context.update({"end_time": int(datetime.now().timestamp()), "file_list": [str(file) for file in files]})
        await self.hooks.run_commands(payload["downloaded_processor"], context)
        if payload["upload_streamers_id"] and files:
            self._schedule_upload(state, payload, context, files)
        elif files and payload["postprocessor"]:
            await self.hooks.run_postprocessors(payload["postprocessor"], files, context)
        if error := self._disk_space_error():
            state.status = "Degraded"
            state.monitor_error = error
        else:
            state.status = "Paused" if state.paused else "Idle"
            state.monitor_error = None

    def _schedule_upload(
        self,
        state: WorkerState,
        payload: dict[str, Any],
        context: dict[str, Any],
        files: list[Path],
    ) -> bool:
        queued = sum(
            not task.done()
            for worker in self.workers.values()
            for task in worker.upload_tasks
        )
        limit = max(1, int(self.config.get("automatic_upload_queue_limit", 8) or 8))
        if queued >= limit:
            state.upload_status = "Error"
            state.upload_error = (
                f"Automatic upload queue is full (limit: {limit}); source files were retained"
            )
            logger.error(
                "Automatic upload queue is full for streamer %s; retained %s source file(s)",
                state.streamer_id,
                len(files),
            )
            return False
        try:
            registered = register_active_uploads(files)
            task = asyncio.create_task(
                self._run_scheduled_upload(state, payload, dict(context), list(files), registered),
                name=f"upload-streamer-{state.streamer_id}",
            )
        except (OSError, ValueError) as exc:
            if "registered" in locals():
                unregister_active_uploads(registered)
            state.upload_status = "Error"
            state.upload_error = str(exc)
            return False
        state.upload_tasks.add(task)
        task.add_done_callback(lambda completed: self._upload_task_done(state, completed))
        return True

    async def _run_scheduled_upload(
        self,
        state: WorkerState,
        payload: dict[str, Any],
        context: dict[str, Any],
        files: list[Path],
        registered: list[Path],
    ) -> None:
        try:
            await self._finish_upload(state, payload, context, files)
        finally:
            unregister_active_uploads(registered)

    def _upload_task_done(self, state: WorkerState, task: asyncio.Task[None]) -> None:
        state.upload_tasks.discard(task)
        if task.cancelled():
            if not state.upload_tasks and state.upload_status in {"Waiting", "Uploading"}:
                state.upload_status = "Idle"
            return
        error = task.exception()
        if error is not None:
            state.upload_status = "Error"
            state.upload_error = redact_sensitive_text(str(error))[:2000]
            logger.error(
                "Upload completion task failed for streamer %s: %s",
                state.streamer_id,
                state.upload_error,
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _finish_upload(
        self,
        state: WorkerState,
        payload: dict[str, Any],
        context: dict[str, Any],
        files: list[Path],
    ) -> None:
        async with state.upload_lock:
            upload_result = await self._upload(state, payload, context, files)
            if upload_result is None:
                return
            steps = payload["postprocessor"]
            if upload_result.account_key == "noop":
                await self.hooks.run_postprocessors(steps or ["rm"], files, context)
                return
            configured_steps = list(steps or [])
            immediate_steps = [
                step
                for step in configured_steps
                if HookRunner._normalize(step)[0] != "rm"
            ]
            await self.hooks.run_postprocessors(immediate_steps, files, context)
            delete_after_review = steps is None or len(immediate_steps) != len(configured_steps)
            if delete_after_review:
                self.submission_reviews.enqueue(upload_result, files)

    async def _wait_for_stream_recovery(self, state: WorkerState, checker: Any) -> bool:
        grace = max(0.0, float(self.config.get("delay", 60) or 0))
        deadline = self._clock() + grace
        required_offline = 1 if grace == 0 else 3
        offline_count = 0
        unknown_count = 0
        checker_sleep = max(1.0, float(self.config.get("checker_sleep", 10) or 10))
        offline_interval = min(
            60.0,
            max(checker_sleep, grace / max(1, required_offline - 1)),
        )

        while not state.paused and not self._closing:
            state.status = "Recovering"
            probe = await self._probe_stream(checker, is_check=False)
            if probe.status is StreamStatus.LIVE and checker.raw_stream_url:
                state.monitor_error = None
                return True
            if probe.status is StreamStatus.UNRECORDABLE:
                state.status = "Unrecordable"
                state.monitor_error = probe.reason or "Live stream is not recordable"
                return False
            if probe.status is StreamStatus.OFFLINE:
                offline_count += 1
                unknown_count = 0
                state.status = "ConfirmingOffline"
                state.monitor_error = None
                if offline_count >= required_offline and self._clock() >= deadline:
                    return False
                await asyncio.sleep(offline_interval)
                continue

            offline_count = 0
            unknown_count += 1
            state.monitor_error = probe.reason or "Live status is temporarily unavailable"
            backoff = min(checker_sleep * (2 ** min(unknown_count - 1, 4)), 60.0)
            await asyncio.sleep(backoff + random.uniform(0, min(checker_sleep, 5.0)))
        return False

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
    ) -> UploadResult | None:
        with self.database.session_factory() as session:
            template = session.get(UploadStreamer, payload["upload_streamers_id"])
            if template is None:
                state.upload_status = "Error"
                state.upload_error = "upload template not found"
                return None
            params = {column.name: getattr(template, column.name) for column in template.__table__.columns}
        for key in ("submit_api", "lines", "threads"):
            params[key] = payload["override"].get(key, self.config.get(key))
        params["submit_interval"] = payload["override"].get(
            "submit_interval",
            self.config.get("submit_interval", 60),
        )
        params["user"] = payload["override"].get("user", self.config.get("user", {}))
        params["_database"] = self.database
        params["source_url"] = context["url"]
        try:
            delay = int(self.config.get("upload_delay", 0) or 0)
            if delay:
                state.upload_status = "Waiting"
                await asyncio.sleep(delay)
            params["title"] = render_upload_text(params.get("title") or context["room_title"], context)
            params["description"] = render_upload_text(params.get("description") or "", context)
            if not params.get("cover_path") and context.get("live_cover_path"):
                params["cover_path"] = context["live_cover_path"]
            limit = max(1, int(self.config.get("max_upload_limit", 8) or 8))
            for attempt in range(1, limit + 1):
                state.upload_status = "Uploading"
                try:
                    async with self.upload_semaphore:
                        result = await upload_files([str(file) for file in files], params, self.paths)
                    state.upload_status = "Idle"
                    state.upload_error = None
                    return result
                except Exception as exc:
                    state.upload_status = "Error"
                    state.upload_error = str(exc)
                    logger.exception(
                        "Upload attempt %s/%s failed for streamer %s",
                        attempt,
                        limit,
                        state.streamer_id,
                    )
                    retryable = is_transient_upload_error(exc)
                    if not retryable:
                        logger.error("Upload error is not retryable; stopping further attempts")
                        break
                    if attempt < limit:
                        await asyncio.sleep(min(2**attempt, 30))
            return None
        finally:
            if state.upload_status in {"Waiting", "Uploading"}:
                state.upload_status = "Idle"
