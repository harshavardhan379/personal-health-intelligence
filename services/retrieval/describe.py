"""Shared event-to-text rendering, used by every intent-specific retrieval path
(temporal.py, comparison.py) so a given fact reads identically no matter which
path surfaced it — and matches apps/web's timeline describeEvent() wording.
"""

from packages.schemas.models import HealthEvent


def describe_event(event: HealthEvent) -> str:
    data = event.data
    if event.event_type == "lab":
        return f"{data.get('test_name')}: {data.get('value')} {data.get('unit')}"
    if event.event_type == "medication":
        dose = f" {data['dose']}" if data.get("dose") else ""
        return f"{data.get('medication')}{dose}"
    if event.event_type == "encounter":
        return str(data.get("encounter_type"))
    if event.event_type == "symptom":
        return f"Symptom: {data.get('symptom')}"
    return str(data)
