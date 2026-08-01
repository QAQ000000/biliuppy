import shutil
from pathlib import Path

from sqlalchemy import inspect, select

from biliup.database import Database
from biliup.database.models import Configuration


def test_fresh_database_schema(tmp_path: Path) -> None:
    database = Database(tmp_path / "data.sqlite3")
    database.migrate()

    tables = set(inspect(database.engine).get_table_names())

    assert {"configuration", "livestreamers", "uploadstreamers", "streamerinfo", "filelist"} <= tables


def test_existing_database_is_adopted_without_losing_configuration(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "data" / "data.sqlite3"
    target = tmp_path / "legacy.sqlite3"
    shutil.copy2(source, target)
    database = Database(target)
    with database.session_factory() as session:
        before = list(session.scalars(select(Configuration.value)))

    database.migrate()

    with database.session_factory() as session:
        after = list(session.scalars(select(Configuration.value)))
    assert after == before
