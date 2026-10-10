"""Seed the database with one synthetic demo user and a couple of confirmed events (YGH-26).

Run against a live Postgres (after `docker compose up` and `alembic upgrade head`):

    python database/seeds/seed.py
"""

import os
import sys
import uuid
from datetime import date, datetime, timedelta

# Run directly with `python database/seeds/seed.py` (not as a module), so the
# repo root needs to be added to sys.path by hand — matches database/migrations/env.py.
sys.path.insert(0, os.getcwd())

from packages.schemas.db import SessionLocal
from packages.schemas.models import Document, HealthEvent, Provenance, User
from services.api.app.core.security import hash_password
from services.retrieval.semantic import sync_event_embedding


def seed() -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter_by(email="demo@example.com").first()
        if existing:
            print("Demo user already seeded, skipping.")
            return

        user = User(
            id=uuid.uuid4(),
            email="demo@example.com",
            hashed_password=hash_password("demo-password-only-for-dev"),
        )
        db.add(user)
        db.flush()

        doc = Document(
            id=uuid.uuid4(),
            user_id=user.id,
            filename="synthetic_lab_report_2026-01-10.pdf",
            mime_type="application/pdf",
            storage_path="seed/synthetic_lab_report.pdf",
            processing_status="processed",
            classification="lab_report",
            classification_confidence=0.95,
        )
        db.add(doc)
        db.flush()

        for offset_days, value in [(0, 7.2), (90, 6.8)]:
            event = HealthEvent(
                id=uuid.uuid4(),
                user_id=user.id,
                document_id=doc.id,
                event_type="lab",
                event_date=date(2026, 1, 10) + timedelta(days=offset_days),
                data={
                    "test_name": "HbA1c",
                    "value": value,
                    "unit": "%",
                    "reference_range": "4.0-5.6",
                },
                confidence=0.9,
                extraction_method="regex",
                review_status="confirmed",
                reviewed_at=datetime.utcnow(),
            )
            db.add(event)
            db.flush()
            db.add(
                Provenance(
                    id=uuid.uuid4(),
                    health_event_id=event.id,
                    document_id=doc.id,
                    page_number=1,
                    quote=f"HbA1c: {value}% (ref 4.0-5.6)",
                )
            )
            db.flush()
            db.refresh(event)
            sync_event_embedding(db, event)

        db.commit()
        print(f"Seeded demo user {user.email} (id={user.id}) with 2 confirmed HbA1c events.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
