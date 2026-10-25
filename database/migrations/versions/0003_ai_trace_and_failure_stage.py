"""ai_traces keep metadata only; documents record which stage failed

AI tracing (YGH-92): `ai_traces.question` was a required column holding the raw question, which
is health content; telemetry must not be a second copy of it. The column is dropped (the table
was never written to) and replaced by what is worth knowing about a call: request id, model,
prompt version, latency, token counts, the ids of the events retrieved, how many claims got each
verification label, the safety action, an outcome status and the LENGTH of the question.

Pipeline failures (YGH-95): `documents.failed_stage` says where processing stopped (reading the
file, parsing, OCR, extraction, saving, or interrupted), so the page can say what to do.

Revision ID: 0003_ai_trace_and_failure_stage
Revises: 0002_event_embeddings
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_ai_trace_and_failure_stage"
down_revision = "0002_event_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("ai_traces", "question")
    op.add_column("ai_traces", sa.Column("request_id", sa.String(64), nullable=True))
    op.add_column("ai_traces", sa.Column("model", sa.String(128), nullable=True))
    op.add_column("ai_traces", sa.Column("prompt_version", sa.String(32), nullable=True))
    op.add_column("ai_traces", sa.Column("llm_ms", sa.Integer, nullable=True))
    op.add_column("ai_traces", sa.Column("input_tokens", sa.Integer, nullable=True))
    op.add_column("ai_traces", sa.Column("output_tokens", sa.Integer, nullable=True))
    op.add_column(
        "ai_traces", sa.Column("retrieval_event_ids", postgresql.JSONB, nullable=True)
    )
    op.add_column("ai_traces", sa.Column("evidence_count", sa.Integer, nullable=True))
    op.add_column("ai_traces", sa.Column("claim_labels", postgresql.JSONB, nullable=True))
    op.add_column("ai_traces", sa.Column("safety_action", sa.String(32), nullable=True))
    op.add_column(
        "ai_traces",
        sa.Column("status", sa.String(24), nullable=False, server_default="ok"),
    )
    op.add_column("ai_traces", sa.Column("question_chars", sa.Integer, nullable=True))
    op.create_index("ix_ai_traces_request_id", "ai_traces", ["request_id"])

    op.add_column("documents", sa.Column("failed_stage", sa.String(32), nullable=True))
    op.add_column(
        "documents",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "processing_started_at")
    op.drop_column("documents", "failed_stage")
    op.drop_index("ix_ai_traces_request_id", table_name="ai_traces")
    for column in ("question_chars", "status", "safety_action", "claim_labels", "evidence_count",
                   "retrieval_event_ids", "output_tokens", "input_tokens", "llm_ms",
                   "prompt_version", "model", "request_id"):
        op.drop_column("ai_traces", column)
    op.add_column("ai_traces", sa.Column("question", sa.Text, nullable=False, server_default=""))
