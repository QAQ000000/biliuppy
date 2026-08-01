"""Normalize Rust uploader names to the pure Python uploader.

Revision ID: 0002_python_uploaders
Revises: 0001_python_baseline
"""

from alembic import op

revision = "0002_python_uploaders"
down_revision = "0001_python_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE uploadstreamers SET uploader = 'bili_web' "
        "WHERE uploader IN ('biliup-rs', 'stream_gears')"
    )


def downgrade() -> None:
    pass
