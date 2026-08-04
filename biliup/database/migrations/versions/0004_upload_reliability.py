"""Persist uploaded parts, submit intervals, and pending reviews.

Revision ID: 0004_upload_reliability
Revises: 0003_background_jobs
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_upload_reliability"
down_revision = "0003_background_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uploadpartcache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("account_key", sa.String(length=80), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("file_hash", "file_size", "account_key"),
    )
    op.create_index("ix_uploadpartcache_file_hash", "uploadpartcache", ["file_hash"])
    op.create_index("ix_uploadpartcache_account_key", "uploadpartcache", ["account_key"])
    op.create_index("ix_uploadpartcache_expires_at", "uploadpartcache", ["expires_at"])

    op.create_table(
        "uploadaccountstate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_key", sa.String(length=80), nullable=False),
        sa.Column("last_submitted_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_uploadaccountstate_account_key",
        "uploadaccountstate",
        ["account_key"],
        unique=True,
    )

    op.create_table(
        "pendingsubmissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("aid", sa.Integer(), nullable=False),
        sa.Column("bvid", sa.String()),
        sa.Column("account_key", sa.String(length=80), nullable=False),
        sa.Column("cookie_path", sa.String(), nullable=False),
        sa.Column("source_files", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("archive_state", sa.Integer()),
        sa.Column("state_description", sa.Text()),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("checked_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pendingsubmissions_aid", "pendingsubmissions", ["aid"], unique=True)
    op.create_index("ix_pendingsubmissions_account_key", "pendingsubmissions", ["account_key"])
    op.create_index("ix_pendingsubmissions_status", "pendingsubmissions", ["status"])


def downgrade() -> None:
    pass
