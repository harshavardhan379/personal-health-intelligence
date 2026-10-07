"""Trend evidence (fixes a gap found testing YGH-83/84/85): TREND questions were
falling through to the same relevance-ranked hybrid_search as everything else,
leaving the LLM to eyeball trend direction from raw numbers itself — exactly
the kind of arithmetic reasoning a small model gets wrong (observed live:
"HbA1c is within target range" while citing 7.2%, which isn't). We already
compute trend direction deterministically in services/trends/trend_engine.py
(M9) — this hands that COMPUTED result to the model instead of asking it to
infer one.
"""

import uuid
from collections import defaultdict

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import HealthEvent
from packages.schemas.queries import CONFIRMED_STATUSES
from services.trends.trend_engine import MeasurementPoint, measurement_history


def search_trend(db: Session, user_id: uuid.UUID) -> list[Evidence]:
    lab_events = (
        db.query(HealthEvent)
        .filter(
            HealthEvent.user_id == user_id,
            HealthEvent.event_type == "lab",
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .all()
    )

    by_test: dict = defaultdict(list)
    for event in lab_events:
        test_name = event.data.get("test_name")
        if test_name:
            by_test[test_name].append(event)

    evidence = []
    for test_name, events in by_test.items():
        points = [
            MeasurementPoint(
                date=e.event_date.isoformat(), value=e.data["value"], unit=e.data["unit"]
            )
            for e in events
        ]
        ordered_points, direction = measurement_history(points)

        history_text = ", ".join(f"{p.date}: {p.value}{p.unit}" for p in ordered_points)
        text = f"{test_name} trend: {direction}. History: {history_text}"

        evidence.append(
            Evidence(
                document_id=str(events[0].document_id),
                page=0,
                text=text,
                event_ids=[str(e.id) for e in events],
                relevance_score=1.0,
                source="trend",
            )
        )
    return evidence
