"""Latency metrics (YGH-93): how long each stage and each route is taking.

In-process and per worker (see packages/observability/metrics.py), so it shows what THIS server
has seen since it started. Signed-in users only. Contains timings and counts, no health content.
"""

from fastapi import APIRouter, Depends

from packages.observability.metrics import STAGES, registry
from packages.schemas.models import User
from services.api.app.core.deps import get_current_user

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("")
def get_metrics(current_user: User = Depends(get_current_user)):
    snapshot = registry.snapshot()
    return {
        "note": "Per server process; resets on restart. Milliseconds. p50/p95 over the last "
                "1000 observations of each.",
        "stages": {name: snapshot[name] for name in STAGES if name in snapshot},
        "requests": {name[len("request "):]: stats for name, stats in snapshot.items()
                     if name.startswith("request ")},
    }
