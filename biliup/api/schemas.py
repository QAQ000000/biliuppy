from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LiveStreamerInput(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int | None = None
    url: str
    remark: str
    filename_prefix: str | None = None
    time_range: str | None = None
    upload_streamers_id: int | None = Field(default=None, validation_alias="upload_id")
    format: str | None = None
    override: dict[str, Any] | None = None
    preprocessor: list[Any] | None = None
    segment_processor: list[Any] | None = None
    downloaded_processor: list[Any] | None = None
    postprocessor: list[Any] | None = None
    opt_args: list[str] | None = None
    excluded_keywords: list[str] | None = None


class UploadStreamerInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    template_name: str
    title: str | None = None
    tid: int | None = None
    copyright: int | None = None
    copyright_source: str | None = None
    cover_path: str | None = None
    description: str | None = None
    dynamic: str | None = None
    dtime: int | None = None
    dolby: int | None = None
    hires: int | None = None
    charging_pay: int | None = None
    no_reprint: int | None = None
    uploader: str | None = "bili_web"
    user_cookie: str | None = None
    tags: list[str] = Field(default_factory=list)
    credits: list[dict[str, Any]] | None = None
    up_selection_reply: int | None = None
    up_close_reply: int | None = None
    up_close_danmu: int | None = None
    extra_fields: str | None = None
    is_only_self: int | None = None


class ConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="allow")


class UserInput(BaseModel):
    key: str = "bilibili-cookies"
    value: str


class Credentials(BaseModel):
    username: str
    password: str
    next: str | None = None


class ManualUploadInput(BaseModel):
    files: list[str]
    params: UploadStreamerInput


def orm_dict(instance: Any) -> dict[str, Any]:
    return {column.name: getattr(instance, column.name) for column in instance.__table__.columns}


def streamer_info_dict(instance: Any) -> dict[str, Any]:
    value = orm_dict(instance)
    if isinstance(value.get("date"), datetime):
        value["date"] = value["date"].isoformat()
    value["files"] = [orm_dict(file) for file in instance.files]
    return value
