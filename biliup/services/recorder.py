from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("biliup.recorder")


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
        command.extend(["-rw_timeout", "20000000", "-i", self.spec.stream_url, "-c", "copy"])
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
        self.process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert self.process.stdout is not None
        try:
            async for raw_line in self.process.stdout:
                line = raw_line.decode(errors="replace").strip().strip("\"'")
                candidate = Path(line)
                if callback and candidate.is_file() and (notified is None or candidate not in notified):
                    await callback(candidate)
                    if notified is not None:
                        notified.add(candidate)
                elif line:
                    logger.debug("[%s] %s", self.spec.name, line)
            return await self.process.wait()
        except BaseException:
            await self.stop()
            raise

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
        output_name = f"{stem}_%03d.{self.spec.format}" if segmented else f"{stem}.{self.spec.format}"
        output = self.spec.output_dir / output_name
        logger.info("Starting FFmpeg recording for %s", self.spec.name)
        if not size_limited:
            return_code = await self._run_process(self._command(output, segmented), on_segment, notified)
            if return_code and not self._stopping:
                raise RuntimeError(f"FFmpeg exited with code {return_code}")
            files = sorted(self.spec.output_dir.glob(f"{stem}*.{self.spec.format}"))
            await self._notify_files(files, on_segment, notified)
            return files

        index = 0
        while not self._stopping:
            part = self.spec.output_dir / f"{stem}_{index:03d}.{self.spec.format}"
            started = time.monotonic()
            return_code = await self._run_process(self._command(part, False, bounded=True))
            if return_code and not self._stopping:
                raise RuntimeError(f"FFmpeg exited with code {return_code}")
            if self._stopping or not part.is_file():
                break
            reached_size = part.stat().st_size >= int(self.spec.file_size * 0.95)
            reached_time = bool(seconds and time.monotonic() - started >= seconds * 0.95)
            await self._notify_files([part], on_segment, notified)
            if not (reached_size or reached_time):
                break
            index += 1
        files = sorted(self.spec.output_dir.glob(f"{stem}*.{self.spec.format}"))
        await self._notify_files(files, on_segment, notified)
        return files

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
