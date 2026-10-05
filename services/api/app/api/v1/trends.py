"""Trends API (YGH-32 query, YGH-34 data for visualization)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from packages.schemas.db import get_db
from packages.schemas.models import HealthEvent, User
from packages.schemas.queries import CONFIRMED_STATUSES
from services.api.app.core.deps import get_current_user
from services.trends.trend_engine import MeasurementPoint, measurement_history

router = APIRouter(prefix="/trends", tags=["trends"])


class TrendPointOut(BaseModel):
    date: str
    value: float
    unit: str


class TrendOut(BaseModel):
    test_name: str
    points: list[TrendPointOut]
    direction: str


@router.get("/{test_name}", response_model=TrendOut)
def get_trend(
    test_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    events = (
        db.query(HealthEvent)
        .filter(
            HealthEvent.user_id == current_user.id,
            HealthEvent.event_type == "lab",
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .all()
    )

    points = [
        MeasurementPoint(
            date=e.event_date.isoformat(), value=e.data["value"], unit=e.data["unit"]
        )
        for e in events
        if e.data.get("test_name", "").lower() == test_name.lower()
    ]

    ordered, direction = measurement_history(points)
    return TrendOut(
        test_name=test_name,
        points=[TrendPointOut(date=p.date, value=p.value, unit=p.unit) for p in ordered],
        direction=direction,
    )
