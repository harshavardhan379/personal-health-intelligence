"""Citations in EXTRACTED mode (framework section 13, decision agreed with the owner).

A system that reads the pipeline's events can only cite them by ids it made up itself; those
events have no id in the ground truth. After the system has answered, the runner maps each such
event to the ground-truth event it corresponds to, using the same matching rule as extraction
scoring (evaluation/matching.py), and rewrites the cited ids. The system never sees the truth.

  matched event      -> that ground-truth event's id (so citation and retrieval scores work)
  unmatched event    -> "unmatched:<id>"  (an extraction false positive: can never be gold)
  id the system made up -> "unknown:<id>"  (a citation of nothing: can never be gold)
"""

from dataclasses import replace

from evaluation.matching import match_events


def truth_id_map(registry: dict, truth_events: list) -> dict:
    """registry: the system's own event id -> PredictedEvent. Returns that id -> truth id."""
    pairs, _, _ = match_events(truth_events, list(registry.values()))
    matched = {id(predicted): truth.event_id for truth, predicted in pairs}
    return {event_id: matched.get(id(event), f"unmatched:{event_id}")
            for event_id, event in registry.items()}


def translate_answer(answer, mapping: dict):
    """A copy of `answer` whose evidence and claim ids are ground-truth ids."""
    def one(event_id):
        return mapping.get(event_id, f"unknown:{event_id}")

    return replace(
        answer,
        evidence=[replace(item, event_ids=[one(i) for i in item.event_ids])
                  for item in answer.evidence],
        claims=[replace(claim, evidence_ids=[one(i) for i in claim.evidence_ids])
                for claim in answer.claims])
