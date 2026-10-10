"""embeddings are per confirmed event, not per raw page

Closes a hole in the review gate: embeddings used to be built from raw page text at
upload time, so semantic search could hand the assistant text from documents whose
events were never confirmed (or were rejected). Embeddings now belong to a health
event (health_events.id) and exist only while that event is confirmed or edited.

Legacy page-level rows cannot be mapped to an event, so they are deleted; re-create
the confirmed-event embeddings with `python database/seeds/backfill_event_embeddings.py`.

Revision ID: 0002_event_embeddings
Revises: 0001_initial_schema
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_event_embeddings"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM embeddings")
    op.add_column(
        "embeddings",
        sa.Column(
            "health_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("health_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_embeddings_health_event_id", "embeddings", ["health_event_id"])


def downgrade() -> None:
    op.drop_index("ix_embeddings_health_event_id", table_name="embeddings")
    op.drop_column("embeddings", "health_event_id")
