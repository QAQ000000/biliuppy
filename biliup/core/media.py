from __future__ import annotations

from pathlib import Path


def is_recording_work_file(path: str | Path) -> bool:
    return Path(path).stem.casefold().endswith(".part")
