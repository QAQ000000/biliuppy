from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

from biliup.core import AppPaths, RecordingConfig, load_recording_config


class ConfigStore(dict[str, Any]):
    """Thread-safe mapping kept for compatibility with existing plugins."""

    def __init__(self, initial: Mapping[str, Any] | None = None):
        super().__init__(initial or {})
        self._lock = RLock()

    def replace(self, value: RecordingConfig | Mapping[str, Any]) -> None:
        data = value.model_dump(mode="python") if isinstance(value, RecordingConfig) else dict(value)
        with self._lock:
            self.clear()
            self.update(data)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self)

    @contextmanager
    def overlay(self, values: Mapping[str, Any] | None) -> Iterator[None]:
        if not values:
            yield
            return
        with self._lock:
            missing = object()
            previous = {key: self.get(key, missing) for key in values}
            self.update(values)
            try:
                yield
            finally:
                for key, value in previous.items():
                    if value is missing:
                        self.pop(key, None)
                    else:
                        self[key] = value


def reload_config(path: str | Path | None = None, *, paths: AppPaths | None = None) -> ConfigStore:
    config.replace(load_recording_config(path, paths=paths))
    return config


config = ConfigStore()
reload_config()
