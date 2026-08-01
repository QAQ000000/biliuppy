# Biliup Engineering Guide

## Product Direction

The backend is pure Python. Do not add Rust, PyO3, Maturin, SQLx, or Cargo dependencies. FFmpeg is the recording process and the Next.js application remains a statically exported frontend.

## Module Boundaries

- `biliup/core`: paths and Pydantic configuration. It must not import API or service modules.
- `biliup/database`: SQLAlchemy models and Alembic migrations. Existing SQLite data must remain upgradeable.
- `biliup/api`: FastAPI transport and compatibility routes. Business logic belongs in services.
- `biliup/services`: scheduling, FFmpeg recording, hooks, login, and upload orchestration.
- `biliup/plugins`: platform URL parsing and Bilibili protocol implementations.
- `app`: Next.js frontend. API paths under `/v1` and `/bili` are compatibility contracts.

## Commands

```text
uv sync --extra dev
uv run pytest -q
uv run ruff check biliup/api biliup/core biliup/database biliup/services tests
npm ci
npm run build
uv run biliup server --no-auth
```

On Windows, `scripts/dev.ps1 -NoAuth` performs the complete development setup.

## Change Rules

- Resolve runtime paths through `AppPaths`; never depend on the process working directory.
- Add an Alembic migration for every schema change. Never replace or recreate a user database.
- Keep YAML and TOML configuration compatible and preserve unknown platform options.
- Keep API response fields compatible with the frontend and add a contract test for changes.
- Never log cookies, access tokens, passwords, or session secrets.
- Network-dependent platform tests must be opt-in; the default test suite must run offline.
