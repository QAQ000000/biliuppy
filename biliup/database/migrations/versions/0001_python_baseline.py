"""Adopt the existing biliup SQLite schema.

Revision ID: 0001_python_baseline
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_python_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "configuration" not in tables:
        op.create_table(
            "configuration",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
        )
        op.create_index("ix_configuration_key", "configuration", ["key"])
    if "uploadstreamers" not in tables:
        op.create_table(
            "uploadstreamers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("template_name", sa.String(), nullable=False),
            sa.Column("title", sa.String()),
            sa.Column("tid", sa.Integer()),
            sa.Column("copyright", sa.Integer()),
            sa.Column("copyright_source", sa.String()),
            sa.Column("cover_path", sa.String()),
            sa.Column("description", sa.Text()),
            sa.Column("dynamic", sa.String()),
            sa.Column("dtime", sa.Integer()),
            sa.Column("dolby", sa.Integer()),
            sa.Column("hires", sa.Integer()),
            sa.Column("charging_pay", sa.Integer()),
            sa.Column("no_reprint", sa.Integer()),
            sa.Column("uploader", sa.String()),
            sa.Column("user_cookie", sa.String()),
            sa.Column("tags", sa.JSON(), nullable=False),
            sa.Column("credits", sa.JSON()),
            sa.Column("up_selection_reply", sa.Integer()),
            sa.Column("up_close_reply", sa.Integer()),
            sa.Column("up_close_danmu", sa.Integer()),
            sa.Column("extra_fields", sa.String()),
            sa.Column("is_only_self", sa.Integer()),
        )
    if "livestreamers" not in tables:
        op.create_table(
            "livestreamers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("url", sa.String(), nullable=False, unique=True),
            sa.Column("remark", sa.String(), nullable=False),
            sa.Column("filename_prefix", sa.String()),
            sa.Column("time_range", sa.String()),
            sa.Column("upload_streamers_id", sa.Integer(), sa.ForeignKey("uploadstreamers.id", ondelete="CASCADE")),
            sa.Column("format", sa.String()),
            sa.Column("override", sa.JSON()),
            sa.Column("preprocessor", sa.JSON()),
            sa.Column("segment_processor", sa.JSON()),
            sa.Column("downloaded_processor", sa.JSON()),
            sa.Column("postprocessor", sa.JSON()),
            sa.Column("opt_args", sa.JSON()),
            sa.Column("excluded_keywords", sa.JSON()),
        )
    if "streamerinfo" not in tables:
        op.create_table(
            "streamerinfo",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("url", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("date", sa.DateTime(), nullable=False),
            sa.Column("live_cover_path", sa.String(), nullable=False, server_default=""),
        )
    if "filelist" not in tables:
        op.create_table(
            "filelist",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("file", sa.String(), nullable=False),
            sa.Column(
                "streamer_info_id",
                sa.Integer(),
                sa.ForeignKey("streamerinfo.id", ondelete="CASCADE"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    pass
