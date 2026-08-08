import asyncio
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from biliup.services.recorder import (
    FFmpegRecorder,
    RecorderProcessError,
    RecorderSpec,
    RecorderStalledError,
    RecorderStorageError,
    duration_seconds,
)


def test_duration_seconds() -> None:
    assert duration_seconds("02:03:04") == 7384
    assert duration_seconds(None) is None


def test_http_recorder_enables_ffmpeg_reconnect(tmp_path: Path) -> None:
    recorder = FFmpegRecorder(
        RecorderSpec(
            name="demo",
            url="https://example.invalid/live",
            title="test",
            stream_url="https://cdn.example.invalid/live.flv",
            headers={},
            output_dir=tmp_path,
        )
    )

    command = recorder._command(tmp_path / "output.flv", segmented=False)

    assert command[command.index("-reconnect") + 1] == "1"
    assert command[command.index("-reconnect_streamed") + 1] == "1"
    assert command[command.index("-reconnect_delay_max") + 1] == "10"


async def test_unexpected_ffmpeg_exit_leaves_incomplete_file_pending(tmp_path: Path, monkeypatch) -> None:
    class EmptyOutput:
        async def readline(self):
            return b""

    class FailedProcess:
        stdout = EmptyOutput()
        returncode = 1

        async def wait(self):
            return self.returncode

    recorder = FFmpegRecorder(
        RecorderSpec(
            name="demo",
            url="https://example.invalid/live",
            title="test",
            stream_url="https://cdn.example.invalid/live.flv",
            headers={},
            output_dir=tmp_path,
            segment_time=None,
        )
    )

    async def fake_create_subprocess_exec(*command, **_kwargs):
        Path(command[-1]).write_bytes(b"incomplete")
        return FailedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    with pytest.raises(RecorderProcessError):
        await recorder.run()

    assert recorder.output_files() == []
    assert [path.read_bytes() for path in recorder.pending_files()] == [b"incomplete"]


async def test_forced_stop_does_not_finalize_incomplete_file(tmp_path: Path, monkeypatch) -> None:
    class EmptyOutput:
        async def readline(self):
            return b""

    class StoppedProcess:
        stdout = EmptyOutput()
        returncode = 1

        async def wait(self):
            return self.returncode

    recorder = FFmpegRecorder(
        RecorderSpec(
            name="demo",
            url="https://example.invalid/live",
            title="test",
            stream_url="https://cdn.example.invalid/live.flv",
            headers={},
            output_dir=tmp_path,
            segment_time=None,
        )
    )
    recorder._stopping = True

    async def fake_create_subprocess_exec(*command, **_kwargs):
        Path(command[-1]).write_bytes(b"interrupted")
        return StoppedProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    assert await recorder.run() == []
    assert [path.read_bytes() for path in recorder.pending_files()] == [b"interrupted"]


async def test_clean_ffmpeg_exit_finalizes_working_file(tmp_path: Path, monkeypatch) -> None:
    class EmptyOutput:
        async def readline(self):
            return b""

    class SuccessfulProcess:
        stdout = EmptyOutput()
        returncode = 0

        async def wait(self):
            return self.returncode

    recorder = FFmpegRecorder(
        RecorderSpec(
            name="demo",
            url="https://example.invalid/live",
            title="test",
            stream_url="https://cdn.example.invalid/live.flv",
            headers={},
            output_dir=tmp_path,
            segment_time=None,
        )
    )

    async def fake_create_subprocess_exec(*command, **_kwargs):
        Path(command[-1]).write_bytes(b"complete")
        return SuccessfulProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    files = await recorder.run()

    assert [path.read_bytes() for path in files] == [b"complete"]
    assert recorder.pending_files() == []


async def test_recorder_stall_watchdog_terminates_ffmpeg(tmp_path: Path, monkeypatch) -> None:
    class NeverEndingOutput:
        async def readline(self):
            await asyncio.sleep(60)
            return b""

    class FakeProcess:
        def __init__(self):
            self.stdout = NeverEndingOutput()
            self.returncode = None
            self.terminated = False

        def terminate(self):
            self.terminated = True
            self.returncode = 1

        def kill(self):
            self.returncode = 1

        async def wait(self):
            while self.returncode is None:
                await asyncio.sleep(0)
            return self.returncode

    process = FakeProcess()

    async def fake_create_subprocess_exec(*_command, **_kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    recorder = FFmpegRecorder(
        RecorderSpec(
            name="demo",
            url="https://example.invalid/live",
            title="test",
            stream_url="https://cdn.example.invalid/live.flv",
            headers={},
            output_dir=tmp_path,
            stall_timeout=0.05,
        )
    )

    with pytest.raises(RecorderStalledError, match="did not grow"):
        await recorder._run_process(["ffmpeg"])

    assert process.terminated is True


async def test_recorder_disk_guard_terminates_ffmpeg(tmp_path: Path, monkeypatch) -> None:
    class NeverEndingOutput:
        async def readline(self):
            await asyncio.sleep(60)
            return b""

    class FakeProcess:
        def __init__(self):
            self.stdout = NeverEndingOutput()
            self.returncode = None
            self.terminated = False

        def terminate(self):
            self.terminated = True
            self.returncode = 1

        def kill(self):
            self.returncode = 1

        async def wait(self):
            while self.returncode is None:
                await asyncio.sleep(0)
            return self.returncode

    process = FakeProcess()
    disk_checks = 0

    async def fake_create_subprocess_exec(*_command, **_kwargs):
        return process

    def fake_disk_usage(_path):
        nonlocal disk_checks
        disk_checks += 1
        free = 2 * 1024**3 if disk_checks == 1 else 512 * 1024**2
        return SimpleNamespace(total=10 * 1024**3, used=0, free=free)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("biliup.services.recorder.shutil.disk_usage", fake_disk_usage)
    recorder = FFmpegRecorder(
        RecorderSpec(
            name="demo",
            url="https://example.invalid/live",
            title="test",
            stream_url="https://cdn.example.invalid/live.flv",
            headers={},
            output_dir=tmp_path,
            stall_timeout=0.05,
            min_free_bytes=1024**3,
        )
    )

    with pytest.raises(RecorderStorageError, match="at least 1 GB"):
        await recorder._run_process(["ffmpeg"])

    assert process.terminated is True


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
def test_ffmpeg_recorder_lifecycle(tmp_path: Path) -> None:
    source = tmp_path / "source.ts"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=10:duration=1",
            "-c:v",
            "mpeg2video",
            "-f",
            "mpegts",
            str(source),
        ],
        check=True,
    )
    output_dir = tmp_path / "downloads"
    recorder = FFmpegRecorder(
        RecorderSpec(
            name="demo",
            url="https://example.invalid/live",
            title="test",
            stream_url=str(source),
            headers={},
            output_dir=output_dir,
            format="ts",
            segment_time=None,
        )
    )

    files = asyncio.run(recorder.run())

    assert len(files) == 1
    assert files[0].is_file()
    assert files[0].stat().st_size > 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
def test_ffmpeg_segment_callbacks_are_once_and_complete(tmp_path: Path) -> None:
    source = tmp_path / "source.ts"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=10:duration=3.4",
            "-c:v",
            "mpeg2video",
            "-g",
            "10",
            "-f",
            "mpegts",
            str(source),
        ],
        check=True,
    )
    output_dir = tmp_path / "downloads"
    seen: list[tuple[Path, int]] = []

    async def on_segment(path: Path) -> None:
        seen.append((path, path.stat().st_size))

    recorder = FFmpegRecorder(
        RecorderSpec(
            name="demo",
            url="https://example.invalid/live",
            title="test/title",
            stream_url=str(source),
            headers={},
            output_dir=output_dir,
            format="ts",
            segment_time="00:00:01",
            filename_prefix="{streamer}-{title}",
        )
    )

    files = asyncio.run(recorder.run(on_segment))

    assert len(files) >= 3
    assert [path for path, _size in seen] == files
    assert len({path for path, _size in seen}) == len(files)
    assert all(size == path.stat().st_size > 0 for path, size in seen)
    assert all(path.name.startswith("demo-test_title_") for path in files)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not installed")
def test_segment_callback_failure_is_propagated(tmp_path: Path) -> None:
    source = tmp_path / "source.ts"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=10:duration=2",
            "-c:v",
            "mpeg2video",
            "-g",
            "10",
            "-f",
            "mpegts",
            str(source),
        ],
        check=True,
    )
    recorder = FFmpegRecorder(
        RecorderSpec(
            name="demo",
            url="https://example.invalid/live",
            title="test",
            stream_url=str(source),
            headers={},
            output_dir=tmp_path / "downloads",
            format="ts",
            segment_time="00:00:01",
        )
    )

    async def fail(_path: Path) -> None:
        raise RuntimeError("segment hook failed")

    with pytest.raises(RuntimeError, match="segment hook failed"):
        asyncio.run(recorder.run(fail))

    assert recorder.process is not None
    assert recorder.process.returncode is not None
