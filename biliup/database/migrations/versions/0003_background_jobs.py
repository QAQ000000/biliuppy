"""Persist background job status across application restarts.

Revision ID: 0003_background_jobs
Revises: 0002_python_uploaders
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_background_jobs"
down_revision = "0002_python_uploaders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backgroundjobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_backgroundjobs_job_id", "backgroundjobs", ["job_id"], unique=True)


def downgrade() -> None:
    pass
