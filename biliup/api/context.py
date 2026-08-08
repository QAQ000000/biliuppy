from __future__ import annotations

import json
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

from sqlalchemy import select

from biliup.config import ConfigStore
from biliup.core import AppPaths, AppSettings, RecordingConfig, load_recording_config
from biliup.database.models import Configuration
from biliup.database.session import Database
from biliup.services import BackgroundJobManager, MediaStorageService, RecordingScheduler


@dataclass(slots=True)
class AppContext:
    settings: AppSettings
    paths: AppPaths
    database: Database
    config: ConfigStore
    scheduler: RecordingScheduler
    media_storage: MediaStorageService
    jobs: BackgroundJobManager
    log_handler: RotatingFileHandler


def load_effective_config(database: Database, settings: AppSettings, paths: AppPaths) -> RecordingConfig:
    with database.session_factory() as session:
        row = session.scalar(
            select(Configuration).where(Configuration.key == "config").order_by(Configuration.id.desc())
        )
    if row:
        return RecordingConfig.model_validate(json.loads(row.value))
    return load_recording_config(settings.recording_config_path(paths), paths=paths)
