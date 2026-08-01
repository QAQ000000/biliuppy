from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from .context import AppContext


def get_context(request: Request) -> AppContext:
    return request.app.state.context


def get_session(request: Request) -> Generator[Session, None, None]:
    context: AppContext = request.app.state.context
    yield from context.database.sessions()
