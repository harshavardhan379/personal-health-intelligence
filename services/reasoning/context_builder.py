"""Assistant context builder (YGH-82).

Combines intent classification (YGH-81) with hybrid retrieval (M10) into one
object the answer generator (YGH-83) will consume. Deliberately never queries
"everything" — hybrid_search already caps results (services/retrieval/ranking.py,
limit=8), which is what keeps this honest to the brief's "never dump the entire
database into the prompt" requirement.

Note: to_prompt_text() renders only the EVIDENCE block, not a full chat prompt.
Building the role-structured chat messages (system/user) that embed this block
is YGH-83's job, once the LLM provider is chosen — see services/reasoning/ once
that lands.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.intent import QueryIntent
from services.reasoning.intent_classifier import classify_intent
from services.retrieval.comparison import search_comparison
from services.retrieval.hybrid import hybrid_search
from services.retrieval.temporal import search_latest, search_temporal, wants_latest
from services.retrieval.trend_evidence import search_trend


@dataclass
class AssistantContext:
    question: str
    intent: QueryIntent
    evidence: list[Evidence]

    def to_prompt_text(self) -> str:
        """Renders the evidence as a numbered, citable block. Each number is what
        the model should cite back (YGH-84 evidence citations).
        """
        if not self.evidence:
            return "No relevant evidence was found in the patient's confirmed records."

        lines = []
        for i, e in enumerate(self.evidence, start=1):
            lines.append(f"[{i}] (page {e.page}, source={e.source}) {e.text}")
        return "\n".join(lines)


def build_context(db: Session, user_id: uuid.UUID, question: str) -> AssistantContext:
    intent = classify_intent(question)

    # Each intent that needs structurally different evidence gets its own
    # retrieval path (YGH-85, YGH-86, trend fix); everything else falls back
    # to the general-purpose relevance search.
    if intent == "TEMPORAL":
        # "latest" wants the newest matching facts first; "first documented" / "what changed"
        # want the date-ordered history (services/retrieval/temporal.py).
        if wants_latest(question):
            evidence = search_latest(db, user_id, question)
        else:
            evidence = search_temporal(db, user_id)
    elif intent == "TREND":
        evidence = search_trend(db, user_id)
    elif intent == "COMPARISON":
        evidence = search_comparison(db, user_id)
    else:
        evidence = hybrid_search(db, user_id, question)

    return AssistantContext(question=question, intent=intent, evidence=evidence)
