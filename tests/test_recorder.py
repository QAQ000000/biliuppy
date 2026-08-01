import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from biliup.services.recorder import FFmpegRecorder, RecorderSpec, duration_seconds


def test_duration_seconds() -> None:
    assert duration_seconds("02:03:04") == 7384
    assert duration_seconds(None) is None


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
