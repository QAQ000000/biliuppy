import sqlite3
from pathlib import Path

from sqlalchemy import inspect, select

from biliup.database import Database
from biliup.database.models import Configuration, UploadStreamer


def test_fresh_database_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "data.sqlite3")
    database.migrate()

    tables = set(inspect(database.engine).get_table_names())

    assert {
        "backgroundjobs",
        "configuration",
        "livestreamers",
        "uploadstreamers",
        "streamerinfo",
        "filelist",
    } <= tables


def test_existing_database_is_adopted_without_losing_configuration(tmp_path: Path) -> None:
    target = tmp_path / "legacy.sqlite3"
    expected = [
        ("config", '{"downloader":"ffmpeg","lines":"bda2"}'),
        ("app", '{"hot_reload":false}'),
    ]
    with sqlite3.connect(target) as connection:
        connection.execute(
            "CREATE TABLE configuration ("
            "id INTEGER PRIMARY KEY, key VARCHAR NOT NULL, value TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO configuration (key, value) VALUES (?, ?)",
            expected,
        )

    database = Database(target)
    with database.session_factory() as session:
        before = list(session.scalars(select(Configuration.value).order_by(Configuration.id)))

    database.migrate()

    with database.session_factory() as session:
        after = list(session.scalars(select(Configuration.value).order_by(Configuration.id)))
    assert before == [value for _, value in expected]
    assert after == before


def test_legacy_uploaders_are_normalized_by_incremental_migration(tmp_path: Path) -> None:
    database = Database(tmp_path / "legacy-uploaders.sqlite3")
    database.migrate("0001_python_baseline")
    with database.session_factory() as session:
        session.add_all(
            [
                UploadStreamer(template_name="rust", uploader="biliup-rs", tags=[]),
                UploadStreamer(template_name="gears", uploader="stream_gears", tags=[]),
            ]
        )
        session.commit()

    database.migrate()

    with database.session_factory() as session:
        uploaders = list(
            session.scalars(select(UploadStreamer.uploader).order_by(UploadStreamer.id))
        )
    assert uploaders == ["bili_web", "bili_web"]
    assert "backgroundjobs" in inspect(database.engine).get_table_names()
