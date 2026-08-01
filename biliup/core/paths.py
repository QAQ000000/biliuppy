from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


def _source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_home() -> Path:
    source_root = _source_root()
    if (source_root / "pyproject.toml").is_file():
        return source_root
    return Path(user_data_path("biliup", appauthor=False))


def _default_frontend() -> Path:
    source_root = _source_root()
    if (source_root / "pyproject.toml").is_file():
        return source_root / "out"
    return Path(__file__).resolve().parents[1] / "web" / "public"


def _resolve_from(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


@dataclass(frozen=True, slots=True)
class AppPaths:
    home: Path
    data: Path
    config: Path
    logs: Path
    downloads: Path
    cache: Path
    database: Path
    frontend: Path

    @classmethod
    def discover(cls, home: str | Path | None = None) -> AppPaths:
        root = Path(home or os.getenv("BILIUP_HOME") or _default_home()).expanduser().resolve()
        data = _resolve_from(root, os.getenv("BILIUP_DATA_DIR", "data"))
        config = _resolve_from(root, os.getenv("BILIUP_CONFIG_DIR", "config"))
        logs = _resolve_from(root, os.getenv("BILIUP_LOG_DIR", "logs"))
        downloads = _resolve_from(root, os.getenv("BILIUP_DOWNLOAD_DIR", "downloads"))
        cache = _resolve_from(root, os.getenv("BILIUP_CACHE_DIR", "cache"))
        database = _resolve_from(root, os.getenv("BILIUP_DATABASE", str(data / "data.sqlite3")))
        frontend_value = os.getenv("BILIUP_FRONTEND_DIR")
        frontend = _resolve_from(root, frontend_value) if frontend_value else _default_frontend()
        return cls(root, data, config, logs, downloads, cache, database, frontend)

    def ensure(self) -> AppPaths:
        for path in (self.home, self.data, self.config, self.logs, self.downloads, self.cache):
            path.mkdir(parents=True, exist_ok=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        return self

    def resolve_user_path(self, value: str | Path, *, base: Path | None = None) -> Path:
        return _resolve_from(base or self.home, value)
