# Architecture

## Runtime

The `biliup` command creates one FastAPI process. The process owns the SQLite connection factory, recording scheduler, plugin registry, and FFmpeg child processes. All backend application logic is Python; FFmpeg is the only external media executable. Next.js is built as static files, embedded in the wheel, and served by FastAPI.

```text
Browser -> FastAPI -> SQLAlchemy -> SQLite
                 |-> RecordingScheduler -> platform checker -> FFmpeg
                 |-> Python Bilibili login/upload client
                 `-> static Next.js export
```

## Module Map

- `api` owns HTTP and WebSocket compatibility contracts.
- `core` owns paths and validated configuration.
- `database` owns persistence models and migrations.
- `services` owns application scheduling and recording lifecycle.
- `platforms` owns live-site detection and stream discovery.
- `integrations` owns external login, metadata, and upload adapters.
- `danmaku` owns chat capture clients and their protocol assets.

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

Each `livestreamers` row owns one scheduler worker. A worker selects a Python platform checker, obtains the live stream URL, runs preprocessor hooks, and starts FFmpeg. Stream probes distinguish `LIVE`, authoritative `OFFLINE`, and transient `UNKNOWN` results; Bilibili uses the unsigned `room_init` endpoint before requesting WBI-signed room details. If FFmpeg exits or its output files stop changing, the worker keeps completed fragments and probes the platform during the configured `delay` grace period. A refreshed live URL resumes the same recording session with bounded exponential backoff, repeated recorder failures open a cooldown circuit, `UNKNOWN` never confirms an offline event, and only repeated explicit `OFFLINE` results finalize the session. The worker then records all generated files in one SQLite history row, runs downloaded hooks, optionally uploads through `bili_web` after the separate `upload_delay`, and runs explicit postprocessors. Pause and shutdown terminate the owned FFmpeg process. Upload retries clear stale errors after success; when all retries fail, recording files are retained and the default deletion hook is skipped.

## Compatibility

Alembic adopts the existing Rust-created tables without recreating them. File-based YAML/TOML streamers are imported only when the database has no streamer rows. Legacy uploader names `biliup-rs` and `stream_gears` are migrated to `bili_web`.

Manual upload job status is stored in SQLite. The application retains the latest 100 terminal jobs; work interrupted by a process restart is marked `Cancelled` instead of being submitted again automatically.
