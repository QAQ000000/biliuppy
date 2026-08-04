from __future__ import annotations

import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import TypeVar

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

T = TypeVar("T")


class Database:
    WRITE_RETRY_DELAYS = (0.05, 0.1, 0.2)

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.url = f"sqlite:///{self.path.as_posix()}"
        self.engine = create_engine(
            self.url,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        event.listen(self.engine, "connect", self._configure_connection)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    @staticmethod
    def _configure_connection(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    @staticmethod
    def _is_sqlite_busy(error: OperationalError) -> bool:
        message = str(error.orig).casefold()
        return "database is locked" in message or "database is busy" in message

    def run_write(self, operation: Callable[[Session], T]) -> T:
        for attempt in range(len(self.WRITE_RETRY_DELAYS) + 1):
            try:
                with self.session_factory.begin() as session:
                    return operation(session)
            except OperationalError as exc:
                if not self._is_sqlite_busy(exc) or attempt >= len(self.WRITE_RETRY_DELAYS):
                    raise
                time.sleep(self.WRITE_RETRY_DELAYS[attempt])
        raise RuntimeError("SQLite write retry loop exhausted")

    def migrate(self, revision: str = "head") -> None:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
        config.set_main_option("sqlalchemy.url", self.url.replace("%", "%%"))
        command.upgrade(config, revision)

    def sessions(self) -> Generator[Session, None, None]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()

    @property
    def bind(self) -> Engine:
        return self.engine
