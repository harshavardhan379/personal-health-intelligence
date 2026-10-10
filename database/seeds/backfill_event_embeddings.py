"""Rebuild semantic-search embeddings for every confirmed/edited health event.

Needed once after migration 0002 (which clears the old page-level embeddings), and
safe to re-run any time: sync_event_embedding is idempotent.

    python database/seeds/backfill_event_embeddings.py
"""

import os
import sys

# Run directly (not as a module), so add the repo root by hand, as seed.py does.
sys.path.insert(0, os.getcwd())

from packages.schemas.db import SessionLocal
from packages.schemas.models import HealthEvent
from packages.schemas.queries import CONFIRMED_STATUSES
from services.retrieval.semantic import sync_event_embedding


def backfill() -> None:
    db = SessionLocal()
    try:
        events = (
            db.query(HealthEvent)
            .filter(HealthEvent.review_status.in_(CONFIRMED_STATUSES))
            .all()
        )
        for event in events:
            sync_event_embedding(db, event)
        db.commit()
        print(f"Indexed {len(events)} confirmed event(s).")
    finally:
        db.close()


if __name__ == "__main__":
    backfill()
