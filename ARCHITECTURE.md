# Architecture

## Runtime

The `biliup` command creates one FastAPI process. The process owns the SQLite connection factory, recording scheduler, plugin registry, and FFmpeg child processes. All backend application logic is Python; FFmpeg is the only external media executable. Next.js is built as static files, embedded in the wheel, and served by FastAPI.

```text
Browser -> FastAPI -> SQLAlchemy -> SQLite
                 |-> RecordingScheduler -> platform checker -> FFmpeg
                 |-> Python Bilibili login/upload client
                 `-> static Next.js export
```

## Filesystem

All mutable paths derive from `BILIUP_HOME` and can be overridden independently:

| Purpose | Default | Override |
| --- | --- | --- |
| Database and credentials | `$BILIUP_HOME/data` | `BILIUP_DATA_DIR` |
| SQLite database | `$BILIUP_HOME/data/data.sqlite3` | `BILIUP_DATABASE` |
| Configuration | `$BILIUP_HOME/config` | `BILIUP_CONFIG_DIR` |
| Recordings | `$BILIUP_HOME/downloads` | `BILIUP_DOWNLOAD_DIR` |
| Logs | `$BILIUP_HOME/logs` | `BILIUP_LOG_DIR` |
| Cache | `$BILIUP_HOME/cache` | `BILIUP_CACHE_DIR` |

In a source checkout, `BILIUP_HOME` defaults to the repository root so the existing `data/data.sqlite3` is adopted. Installed packages use the operating system user data directory.

## Recording Lifecycle

Each `livestreamers` row owns one scheduler worker. A worker selects a Python platform checker, obtains the live stream URL, runs preprocessor hooks, starts FFmpeg, records generated files in SQLite, runs downloaded hooks, optionally uploads through `bili_web`, then runs explicit postprocessors. Pause and shutdown terminate the owned FFmpeg process. Upload retries clear stale errors after success; when all retries fail, recording files are retained and the default deletion hook is skipped.

## Compatibility

Alembic adopts the existing Rust-created tables without recreating them. File-based YAML/TOML streamers are imported only when the database has no streamer rows. Legacy uploader names `biliup-rs` and `stream_gears` are migrated to `bili_web`.
