from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from biliup.core import RecordingConfig
from biliup.database.models import Configuration

from ..context import AppContext
from ..dependencies import get_context, get_session

router = APIRouter()


@router.get("/v1/configuration")
def get_configuration(context: AppContext = Depends(get_context)) -> dict:
    return context.config.snapshot()


@router.put("/v1/configuration")
async def put_configuration(
    payload: dict,
    session: Session = Depends(get_session),
    context: AppContext = Depends(get_context),
) -> dict:
    validated = RecordingConfig.model_validate(payload)
    rows = list(session.scalars(select(Configuration).where(Configuration.key == "config")))
    if len(rows) > 1:
        raise HTTPException(409, "有多个空间配置同时存在 (key='config')")
    value = validated.model_dump_json(exclude_none=True)
    if rows:
        rows[0].value = value
    else:
        session.add(Configuration(key="config", value=value))
    session.commit()
    context.config.replace(validated)
    context.log_handler.maxBytes = validated.log_file_max_size_mb * 1024 * 1024
    return json.loads(value)
