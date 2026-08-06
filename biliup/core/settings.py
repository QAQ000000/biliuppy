from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import tomli_w
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .paths import AppPaths


class StreamerConfig(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    url: list[str] = Field(default_factory=list)
    title: str | None = None
    tid: int | None = None
    copyright: int | None = None
    cover_path: str | None = None
    description: str | None = None
    credits: list[dict[str, Any]] | None = None
    dynamic: str | None = None
    uploader: str | None = None
    filename_prefix: str | None = None
    user_cookie: str | None = None
    tags: list[str] | None = None
    time_range: str | None = None
    excluded_keywords: list[str] | None = None
    preprocessor: list[Any] | None = None
    segment_processor: list[Any] | None = None
    downloaded_processor: list[Any] | None = None
    postprocessor: list[Any] | None = None
    format: str | None = None
    opt_args: list[str] | None = None
    override: dict[str, Any] | None = None

    @field_validator("url", mode="before")
    @classmethod
    def normalize_urls(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return [value] if isinstance(value, str) else list(value)


class RecordingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    downloader: str = "ffmpeg"
    file_size: int | None = 2_621_440_000
    segment_time: str | None = "02:00:00"
    filtering_threshold: int = 20
    filename_prefix: str = "{streamer}%Y-%m-%d %H_%M_%S{title}"
    segment_processor_parallel: bool = False
    segment_processor_concurrency: int = 4
    hook_timeout: int = 300
    uploader: str = "Noop"
    submit_api: str = "web"
    lines: str = "AUTO"
    threads: int = Field(default=3, ge=1, le=8)
    delay: int = 60
    upload_delay: int = Field(default=0, ge=0, le=86_400)
    submit_interval: int = Field(default=60, ge=0, le=3_600)
    event_loop_interval: int = 30
    checker_sleep: int = 10
    checker_concurrency: int = Field(default=3, ge=1, le=100)
    recorder_stall_timeout: int = Field(default=90, ge=0, le=3_600)
    recorder_retry_limit: int = Field(default=10, ge=1, le=100)
    recorder_retry_backoff: int = Field(default=5, ge=1, le=300)
    min_free_disk_gb: int = Field(default=5, ge=0, le=10_240)
    pool1_size: int = 5
    pool2_size: int = 3
    max_upload_limit: int = 8
    manual_upload_queue_limit: int = Field(default=8, ge=1, le=100)
    log_file_max_size_mb: int = Field(default=10, ge=1, le=10_240)
    history_max_records: int = Field(default=10_000, ge=1, le=1_000_000)
    use_live_cover: bool = False
    streamers: dict[str, StreamerConfig] = Field(default_factory=dict)
    user: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_nulls(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        nullable_fields = {"file_size", "segment_time"}
        return {key: item for key, item in value.items() if item is not None or key in nullable_fields}


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BILIUP_", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 19159
    home: Path | None = None
    config_file: Path | None = None
    cors_origin: str = "http://localhost:3000"
    log_level: str = "INFO"
    auth_enabled: bool = True
    scheduler_enabled: bool = True
    session_secret: str | None = None
    check_interval: float = 30.0

    def paths(self) -> AppPaths:
        return AppPaths.discover(self.home).ensure()

    def recording_config_path(self, paths: AppPaths | None = None) -> Path | None:
        app_paths = paths or self.paths()
        if self.config_file:
            return app_paths.resolve_user_path(self.config_file)
        candidates = (
            app_paths.config / "config.yaml",
            app_paths.config / "config.toml",
            app_paths.home / "config.yaml",
            app_paths.home / "config.toml",
        )
        return next((candidate for candidate in candidates if candidate.is_file()), None)


def _read_document(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        if path.suffix.lower() == ".toml":
            return tomllib.load(stream)
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(stream) or {}
    raise ValueError(f"Unsupported configuration format: {path.suffix}")


def load_recording_config(path: str | Path | None = None, *, paths: AppPaths | None = None) -> RecordingConfig:
    app_paths = paths or AppPaths.discover()
    selected = app_paths.resolve_user_path(path) if path else AppSettings().recording_config_path(app_paths)
    data = _read_document(selected) if selected and selected.is_file() else {}
    return RecordingConfig.model_validate(data)


def save_recording_config(
    config: RecordingConfig,
    path: str | Path,
    *,
    paths: AppPaths | None = None,
) -> None:
    app_paths = paths or AppPaths.discover()
    target = app_paths.resolve_user_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(config.model_dump_json(exclude_none=True))
    if target.suffix.lower() == ".toml":
        target.write_text(tomli_w.dumps(data), encoding="utf-8")
    elif target.suffix.lower() in {".yaml", ".yml"}:
        target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    else:
        raise ValueError(f"Unsupported configuration format: {target.suffix}")
