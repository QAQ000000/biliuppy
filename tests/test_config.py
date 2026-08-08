from pathlib import Path

from sqlalchemy import select

from biliup.core import AppPaths, AppSettings, RecordingConfig, load_recording_config
from biliup.core.settings import save_recording_config
from biliup.database import Database
from biliup.database.models import LiveStreamer, UploadStreamer
from biliup.services.config_import import import_legacy_streamers


def test_paths_do_not_depend_on_current_directory(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    paths = AppPaths.discover(home).ensure()

    assert paths.home == home.resolve()
    assert paths.database == (home / "data" / "data.sqlite3").resolve()
    assert paths.logs.is_dir()
    assert paths.downloads.is_dir()


def test_relative_environment_paths_are_anchored_to_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("BILIUP_DATA_DIR", "runtime-data")
    monkeypatch.setenv("BILIUP_CONFIG_DIR", "settings")
    monkeypatch.setenv("BILIUP_LOG_DIR", "runtime-logs")
    monkeypatch.setenv("BILIUP_DOWNLOAD_DIR", "records")
    monkeypatch.setenv("BILIUP_CACHE_DIR", "runtime-cache")
    monkeypatch.setenv("BILIUP_DATABASE", "runtime-data/custom.sqlite3")

    paths = AppPaths.discover(home).ensure()

    assert paths.data == (home / "runtime-data").resolve()
    assert paths.config == (home / "settings").resolve()
    assert paths.logs == (home / "runtime-logs").resolve()
    assert paths.downloads == (home / "records").resolve()
    assert paths.cache == (home / "runtime-cache").resolve()
    assert paths.database == (home / "runtime-data" / "custom.sqlite3").resolve()


def test_relative_explicit_config_is_anchored_to_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    home.mkdir()
    elsewhere.mkdir()
    (home / "custom.yaml").write_text("downloader: streamlink\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)

    config = load_recording_config("custom.yaml", paths=AppPaths.discover(home))

    assert config.downloader == "streamlink"


def test_relative_saved_config_is_anchored_to_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    home.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    paths = AppPaths.discover(home)

    save_recording_config(load_recording_config(paths=paths), "config/generated.yaml", paths=paths)

    assert (home / "config" / "generated.yaml").is_file()
    assert not (elsewhere / "config" / "generated.yaml").exists()


def test_yaml_and_toml_are_compatible(tmp_path: Path) -> None:
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "downloader: ffmpeg\nstreamers:\n  demo:\n    url: https://example.com/live\ncustom_key: kept\n",
        encoding="utf-8",
    )

    config = load_recording_config(yaml_path, paths=AppPaths.discover(tmp_path).ensure())
    toml_path = tmp_path / "config.toml"
    save_recording_config(config, toml_path)
    restored = load_recording_config(toml_path, paths=AppPaths.discover(tmp_path).ensure())

    assert restored.downloader == "ffmpeg"
    assert restored.streamers["demo"].url == ["https://example.com/live"]
    assert restored.model_extra["custom_key"] == "kept"


def test_settings_find_legacy_root_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("downloader: streamlink\n", encoding="utf-8")
    settings = AppSettings(home=tmp_path)

    assert settings.recording_config_path(settings.paths()) == config_path


def test_legacy_zero_pool_sizes_are_normalized() -> None:
    config = RecordingConfig.model_validate({"pool1_size": 0, "pool2_size": "0"})

    assert config.pool1_size == 1
    assert config.pool2_size == 1


def test_file_based_streamers_are_imported_once(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
downloader: ffmpeg
uploader: bili_web
streamers:
  demo:
    url:
      - https://example.com/one
      - https://example.com/two
    title: "{title}"
    user_cookie: data/cookies.json
    tags: [biliup]
""",
        encoding="utf-8",
    )
    config = load_recording_config(config_path, paths=AppPaths.discover(tmp_path).ensure())
    database = Database(tmp_path / "data" / "data.sqlite3")
    database.migrate()

    assert import_legacy_streamers(database, config) == 2
    assert import_legacy_streamers(database, config) == 0
    with database.session_factory() as session:
        assert len(session.scalars(select(LiveStreamer)).all()) == 2
        assert len(session.scalars(select(UploadStreamer)).all()) == 1
