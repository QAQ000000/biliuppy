from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("biliup.recorder")


class RecorderError(RuntimeError):
    pass


class RecorderProcessError(RecorderError):
    def __init__(self, return_code: int):
        self.return_code = return_code
        super().__init__(f"FFmpeg exited with code {return_code}")


class RecorderStalledError(RecorderError):
    def __init__(self, timeout: float):
        self.timeout = timeout
        super().__init__(f"FFmpeg output did not grow for {timeout:g} seconds")


class RecorderStorageError(RecorderError):
    def __init__(self, free_bytes: int, required_bytes: int):
        self.free_bytes = free_bytes
        self.required_bytes = required_bytes
        free_gb = free_bytes / 1024**3
        required_gb = required_bytes / 1024**3
        super().__init__(f"Free disk space is {free_gb:.2f} GB; at least {required_gb:g} GB is required")


def duration_seconds(value: str | int | None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid duration: {value}")
    hours, minutes, seconds = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + seconds


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:120] or "recording"


@dataclass(slots=True)
class RecorderSpec:
    name: str
    url: str
    title: str
    stream_url: str
    headers: dict[str, str]
    output_dir: Path
    format: str = "flv"
    segment_time: str | int | None = "02:00:00"
    file_size: int | None = None
    filename_prefix: str | None = None
    extra_args: list[str] | None = None
    stall_timeout: float | None = 90
    min_free_bytes: int = 0


SegmentCallback = Callable[[Path], Awaitable[None]]


class FFmpegRecorder:
    def __init__(self, spec: RecorderSpec):
        self.spec = spec
        self.process: asyncio.subprocess.Process | None = None
        self._stopping = False
        self._stem: str | None = None

    def prepare_stem(self) -> str:
        if self._stem is not None:
            return self._stem
        self.spec.output_dir.mkdir(parents=True, exist_ok=True)
        template = self.spec.filename_prefix or "{streamer}%Y-%m-%d_%H-%M-%S{title}"
        try:
            rendered = template.format(
                streamer=self.spec.name,
                title=self.spec.title,
                url=self.spec.url,
            )
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Invalid filename_prefix: {template}") from exc
        base = safe_filename(time.strftime(rendered, time.localtime()))
        candidate = base
        index = 1
        while any(self.spec.output_dir.glob(f"{candidate}*.{self.spec.format}")):
            candidate = safe_filename(f"{base}_{index}")
            index += 1
        self._stem = candidate
        return candidate

    def _command(self, output: Path, segmented: bool, bounded: bool = False) -> list[str]:
        command = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-y"]
        if self.spec.headers:
            header_text = "".join(f"{key}: {value}\r\n" for key, value in self.spec.headers.items())
            command.extend(["-headers", header_text])
        command.extend(["-rw_timeout", "20000000"])
        if self.spec.stream_url.startswith(("http://", "https://")):
            command.extend(["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"])
        command.extend(["-i", self.spec.stream_url, "-c", "copy"])
        if self.spec.format == "mp4":
            command.extend(["-bsf:a", "aac_adtstoasc"])
        command.extend(self.spec.extra_args or [])
        if bounded:
            seconds = duration_seconds(self.spec.segment_time)
            if seconds:
                command.extend(["-t", str(seconds)])
            if self.spec.file_size:
                command.extend(["-fs", str(self.spec.file_size)])
        if segmented:
            command.extend(
                [
                    "-f",
                    "segment",
                    "-segment_list",
                    "pipe:1",
                    "-segment_list_type",
                    "flat",
                    "-segment_time",
                    str(duration_seconds(self.spec.segment_time)),
                    "-reset_timestamps",
                    "1",
                ]
            )
        command.append(str(output))
        return command

    async def _run_process(
        self,
        command: list[str],
        callback: SegmentCallback | None = None,
        notified: set[Path] | None = None,
    ) -> int:
        self._check_free_space()
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert self.process.stdout is not None
        stall_timeout = max(0.0, float(self.spec.stall_timeout or 0))
        monitor_enabled = bool(stall_timeout or self.spec.min_free_bytes)
        progress_interval = min(5.0, max(0.1, stall_timeout / 3)) if stall_timeout else 5.0
        if not monitor_enabled:
            progress_interval = None
        last_size = self._output_size()
        last_progress = time.monotonic()
        last_checked = last_progress
        try:
            while True:
                try:
                    if progress_interval is None:
                        raw_line = await self.process.stdout.readline()
                    else:
                        raw_line = await asyncio.wait_for(
                            self.process.stdout.readline(),
                            timeout=progress_interval,
                        )
                except asyncio.TimeoutError:
                    raw_line = None

                if raw_line == b"":
                    break
                if raw_line:
                    line = raw_line.decode(errors="replace").strip().strip("\"'")
                    candidate = Path(line)
                    if callback and candidate.is_file() and (notified is None or candidate not in notified):
                        candidate = self._finalize_file(candidate)
                        await callback(candidate)
                        if notified is not None:
                            notified.add(candidate)
                    elif line:
                        logger.debug("[%s] %s", self.spec.name, line)

                now = time.monotonic()
                if progress_interval is not None and now - last_checked >= progress_interval:
                    self._check_free_space()
                    current_size = self._output_size()
                    if current_size != last_size:
                        last_size = current_size
                        last_progress = now
                    last_checked = now
                    if now - last_progress >= stall_timeout:
                        logger.error(
                            "FFmpeg recording for %s stalled for %.1f seconds",
                            self.spec.name,
                            stall_timeout,
                        )
                        self.process.terminate()
                        try:
                            await asyncio.wait_for(self.process.wait(), timeout=10)
                        except asyncio.TimeoutError:
                            self.process.kill()
                            await self.process.wait()
                        raise RecorderStalledError(stall_timeout)
            return_code = await self.process.wait()
            if return_code == 0:
                self._finalize_pending_files()
            return return_code
        except BaseException:
            await self.stop()
            raise

    def _check_free_space(self) -> None:
        required = max(0, int(self.spec.min_free_bytes or 0))
        if not required:
            return
        free = shutil.disk_usage(self.spec.output_dir).free
        if free < required:
            raise RecorderStorageError(free, required)

    def _output_size(self) -> int:
        total = 0
        for file in [*self.output_files(), *self.pending_files()]:
            try:
                total += file.stat().st_size
            except OSError:
                continue
        return total

    async def _notify_files(
        self,
        files: list[Path],
        callback: SegmentCallback | None,
        notified: set[Path],
    ) -> None:
        if callback is None:
            return
        for file in files:
            if file not in notified:
                await callback(file)
                notified.add(file)

    async def run(self, on_segment: SegmentCallback | None = None) -> list[Path]:
        stem = self.prepare_stem()
        notified: set[Path] = set()
        seconds = duration_seconds(self.spec.segment_time)
        size_limited = bool(self.spec.file_size and self.spec.file_size > 0)
        segmented = bool(seconds and seconds > 0 and not size_limited)
        output_name = (
            f"{stem}_%03d.part.{self.spec.format}"
            if segmented
            else f"{stem}.part.{self.spec.format}"
        )
        output = self.spec.output_dir / output_name
        logger.info("Starting FFmpeg recording for %s", self.spec.name)
        if not size_limited:
            return_code = await self._run_process(self._command(output, segmented), on_segment, notified)
            if return_code and not self._stopping:
                raise RecorderProcessError(return_code)
            files = self.output_files()
            await self._notify_files(files, on_segment, notified)
            return files

        index = 0
        while not self._stopping:
            part = self.spec.output_dir / f"{stem}_{index:03d}.part.{self.spec.format}"
            started = time.monotonic()
            return_code = await self._run_process(self._command(part, False, bounded=True))
            if return_code and not self._stopping:
                raise RecorderProcessError(return_code)
            completed_part = self._final_path(part)
            if self._stopping or not completed_part.is_file():
                break
            reached_size = completed_part.stat().st_size >= int(self.spec.file_size * 0.95)
            reached_time = bool(seconds and time.monotonic() - started >= seconds * 0.95)
            await self._notify_files([completed_part], on_segment, notified)
            if not (reached_size or reached_time):
                break
            index += 1
        files = self.output_files()
        await self._notify_files(files, on_segment, notified)
        return files

    def output_files(self) -> list[Path]:
        stem = self.prepare_stem()
        return sorted(
            file
            for file in self.spec.output_dir.glob(f"{stem}*.{self.spec.format}")
            if f".part.{self.spec.format}" not in file.name
        )

    def pending_files(self) -> list[Path]:
        stem = self.prepare_stem()
        return sorted(self.spec.output_dir.glob(f"{stem}*.part.{self.spec.format}"))

    def _final_path(self, path: Path) -> Path:
        marker = f".part.{self.spec.format}"
        if not path.name.endswith(marker):
            return path
        return path.with_name(f"{path.name[:-len(marker)]}.{self.spec.format}")

    def _finalize_file(self, path: Path) -> Path:
        final = self._final_path(path)
        if final == path:
            return path
        path.replace(final)
        return final

    def _finalize_pending_files(self) -> list[Path]:
        return [self._finalize_file(path) for path in self.pending_files()]

    async def stop(self) -> None:
        self._stopping = True
        if not self.process or self.process.returncode is not None:
            return
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except asyncio.TimeoutError:
            self.process.kill()
            await self.process.wait()
