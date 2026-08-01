from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Configuration(Base):
    __tablename__ = "configuration"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class UploadStreamer(Base):
    __tablename__ = "uploadstreamers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    template_name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    tid: Mapped[int | None] = mapped_column(Integer)
    copyright: Mapped[int | None] = mapped_column(Integer)
    copyright_source: Mapped[str | None] = mapped_column(String)
    cover_path: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    dynamic: Mapped[str | None] = mapped_column(String)
    dtime: Mapped[int | None] = mapped_column(Integer)
    dolby: Mapped[int | None] = mapped_column(Integer)
    hires: Mapped[int | None] = mapped_column(Integer)
    charging_pay: Mapped[int | None] = mapped_column(Integer)
    no_reprint: Mapped[int | None] = mapped_column(Integer)
    uploader: Mapped[str | None] = mapped_column(String)
    user_cookie: Mapped[str | None] = mapped_column(String)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    credits: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    up_selection_reply: Mapped[int | None] = mapped_column(Integer)
    up_close_reply: Mapped[int | None] = mapped_column(Integer)
    up_close_danmu: Mapped[int | None] = mapped_column(Integer)
    extra_fields: Mapped[str | None] = mapped_column(String)
    is_only_self: Mapped[int | None] = mapped_column(Integer)

    live_streamers: Mapped[list[LiveStreamer]] = relationship(back_populates="upload_streamer")


class LiveStreamer(Base):
    __tablename__ = "livestreamers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    remark: Mapped[str] = mapped_column(String, nullable=False)
    filename_prefix: Mapped[str | None] = mapped_column(String)
    time_range: Mapped[str | None] = mapped_column(String)
    upload_streamers_id: Mapped[int | None] = mapped_column(
        ForeignKey("uploadstreamers.id", ondelete="CASCADE")
    )
    format: Mapped[str | None] = mapped_column(String)
    override: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    preprocessor: Mapped[list[Any] | None] = mapped_column(JSON)
    segment_processor: Mapped[list[Any] | None] = mapped_column(JSON)
    downloaded_processor: Mapped[list[Any] | None] = mapped_column(JSON)
    postprocessor: Mapped[list[Any] | None] = mapped_column(JSON)
    opt_args: Mapped[list[str] | None] = mapped_column(JSON)
    excluded_keywords: Mapped[list[str] | None] = mapped_column(JSON)

    upload_streamer: Mapped[UploadStreamer | None] = relationship(back_populates="live_streamers")


class StreamerInfo(Base):
    __tablename__ = "streamerinfo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    live_cover_path: Mapped[str] = mapped_column(String, nullable=False, default="")

    files: Mapped[list[FileItem]] = relationship(
        back_populates="streamer_info", cascade="all, delete-orphan"
    )


class FileItem(Base):
    __tablename__ = "filelist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file: Mapped[str] = mapped_column(String, nullable=False)
    streamer_info_id: Mapped[int] = mapped_column(
        ForeignKey("streamerinfo.id", ondelete="CASCADE"), nullable=False
    )

    streamer_info: Mapped[StreamerInfo] = relationship(back_populates="files")
