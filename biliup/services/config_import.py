from __future__ import annotations

from sqlalchemy import func, select

from biliup.core import RecordingConfig
from biliup.database.models import LiveStreamer, UploadStreamer
from biliup.database.session import Database

UPLOAD_FIELDS = {
    "title",
    "tid",
    "copyright",
    "copyright_source",
    "cover_path",
    "description",
    "dynamic",
    "dtime",
    "dolby",
    "hires",
    "charging_pay",
    "no_reprint",
    "uploader",
    "user_cookie",
    "tags",
    "credits",
    "up_selection_reply",
    "up_close_reply",
    "up_close_danmu",
    "extra_fields",
    "is_only_self",
}


def import_legacy_streamers(database: Database, config: RecordingConfig) -> int:
    """Import file-based streamers once, while leaving populated databases untouched."""
    if not config.streamers:
        return 0
    with database.session_factory.begin() as session:
        if session.scalar(select(func.count()).select_from(LiveStreamer)):
            return 0
        imported = 0
        global_values = config.model_dump(mode="python", exclude={"streamers", "user"})
        for name, streamer_config in config.streamers.items():
            values = streamer_config.model_dump(mode="python", exclude_none=True)
            uploader_name = values.get("uploader", global_values.get("uploader", "Noop"))
            if uploader_name in {"biliup-rs", "stream_gears"}:
                uploader_name = "bili_web"
            template_id = None
            if uploader_name != "Noop" or values.get("user_cookie"):
                upload_values = {
                    key: values.get(key, global_values.get(key))
                    for key in UPLOAD_FIELDS
                    if values.get(key, global_values.get(key)) is not None
                }
                upload_values["uploader"] = uploader_name
                upload_values.setdefault("tags", [])
                template = UploadStreamer(template_name=name, **upload_values)
                session.add(template)
                session.flush()
                template_id = template.id
            for url in streamer_config.url:
                session.add(
                    LiveStreamer(
                        url=url,
                        remark=name,
                        filename_prefix=values.get("filename_prefix", global_values.get("filename_prefix")),
                        time_range=values.get("time_range"),
                        upload_streamers_id=template_id,
                        format=values.get("format"),
                        override=values.get("override"),
                        preprocessor=values.get("preprocessor"),
                        segment_processor=values.get("segment_processor"),
                        downloaded_processor=values.get("downloaded_processor"),
                        postprocessor=values.get("postprocessor"),
                        opt_args=values.get("opt_args"),
                        excluded_keywords=values.get("excluded_keywords"),
                    )
                )
                imported += 1
    return imported
