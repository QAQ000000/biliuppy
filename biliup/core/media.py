from __future__ import annotations

from pathlib import Path

MEDIA_EXTENSIONS = frozenset({".mp4", ".flv", ".3gp", ".webm", ".mkv", ".ts"})


def is_recording_work_file(path: str | Path) -> bool:
    target = Path(path)
    return target.suffix.casefold() == ".part" or target.stem.casefold().endswith(".part")


def is_media_file(path: str | Path) -> bool:
    target = Path(path)
    if target.suffix.casefold() == ".part":
        return Path(target.stem).suffix.casefold() in MEDIA_EXTENSIONS
    return target.suffix.casefold() in MEDIA_EXTENSIONS


def completed_path_for_work_file(path: str | Path) -> Path:
    target = Path(path)
    if target.suffix.casefold() == ".part":
        return target.with_suffix("")
    marker = ".part"
    if not target.stem.casefold().endswith(marker):
        raise ValueError(f"Not a recording work file: {target.name}")
    return target.with_name(f"{target.stem[:-len(marker)]}{target.suffix}")


def media_sidecar_path(path: str | Path) -> Path:
    target = Path(path)
    if is_recording_work_file(target):
        target = completed_path_for_work_file(target)
    return target.with_suffix(".xml")
