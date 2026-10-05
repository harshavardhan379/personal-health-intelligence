"""initial schema

Hand-written against packages/schemas/models.py (no live Postgres was available to
autogenerate this from — verify with `alembic check` once `docker compose up` is running,
and regenerate if it drifts).

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("processing_status", sa.String(32), nullable=False, server_default="uploaded"),
        sa.Column("classification", sa.String(64), nullable=True),
        sa.Column("classification_confidence", sa.Float, nullable=True),
        sa.Column("processing_error", sa.Text, nullable=True),
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])

    op.create_table(
        "document_pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False, server_default=""),
        sa.Column("is_ocr", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_document_pages_document_id", "document_pages", ["document_id"])

    op.create_table(
        "health_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("event_date_precision", sa.String(8), nullable=False, server_default="day"),
        sa.Column("data", postgresql.JSONB, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("extraction_method", sa.String(32), nullable=False, server_default="regex"),
        sa.Column("review_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_health_events_user_id", "health_events", ["user_id"])
    op.create_index("ix_health_events_review_status", "health_events", ["review_status"])

    op.create_table(
        "provenance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "health_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("health_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id")),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
    )

    op.create_table(
        "embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id")),
        sa.Column("page_number", sa.Integer, nullable=False),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("vector", Vector(EMBEDDING_DIM), nullable=False),
    )
    op.create_index("ix_embeddings_user_id", "embeddings", ["user_id"])
    op.execute(
        "CREATE INDEX ix_embeddings_vector ON embeddings "
        "USING ivfflat (vector vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "ai_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("intent", sa.String(32), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False, server_default="hosted"),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("token_usage", sa.Integer, nullable=True),
        sa.Column("verification_result", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ai_traces_user_id", "ai_traces", ["user_id"])


def downgrade() -> None:
    op.drop_table("ai_traces")
    op.drop_table("embeddings")
    op.drop_table("provenance")
    op.drop_table("health_events")
    op.drop_table("document_pages")
    op.drop_table("documents")
    op.drop_table("users")
