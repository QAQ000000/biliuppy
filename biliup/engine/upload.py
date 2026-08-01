from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

from biliup.config import config
from biliup.core import AppPaths

logger = logging.getLogger("biliup")
MEDIA_EXTENSIONS = {".mp4", ".flv", ".3gp", ".webm", ".mkv", ".ts"}


class UploadBase:
    class FileInfo(NamedTuple):
        video: str
        danmaku: str | None

    def __init__(self, principal, data, persistence_path=None, postprocessor=None):
        self.principal = principal
        self.persistence_path = persistence_path
        self.data: dict = data
        self.post_processor = postprocessor

    @staticmethod
    def file_list(index: str, directory: str | Path | None = None) -> list[FileInfo]:
        root = Path(directory).resolve() if directory else AppPaths.discover().ensure().downloads
        threshold = float(config.get("filtering_threshold", 0)) * 1024 * 1024
        results: list[UploadBase.FileInfo] = []
        for path in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime):
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS or index not in path.name:
                continue
            if path.stat().st_size <= threshold:
                logger.info("Skip file below upload threshold: %s", path)
                continue
            danmaku = path.with_suffix(".xml")
            results.append(UploadBase.FileInfo(str(path), str(danmaku) if danmaku.is_file() else None))
        return results

    @staticmethod
    def remove_filelist(file_list: list[FileInfo]) -> None:
        for item in file_list:
            Path(item.video).unlink(missing_ok=True)
            if item.danmaku:
                Path(item.danmaku).unlink(missing_ok=True)

    def upload(self, file_list: list[FileInfo]) -> list[FileInfo]:
        raise NotImplementedError

    def start(self) -> list[FileInfo]:
        file_list = self.file_list(self.principal)
        return self.upload(file_list) if file_list else []
