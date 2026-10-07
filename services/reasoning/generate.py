"""Structured answer generation (YGH-83).

build_messages() turns an AssistantContext into a proper role-structured chat
prompt (system + user) — see the discussion in context_builder.py for why this
is a separate step from evidence formatting. generate_answer() is the single
entry point everything else (the API route, later M12 verification) calls.
"""

import json
import logging
import uuid

from sqlalchemy.orm import Session

from packages.schemas.answer import Answer
from services.reasoning.context_builder import AssistantContext, build_context
from services.reasoning.providers import LLMProviderError, get_llm_provider

logger = logging.getLogger("app.reasoning.generate")

_SYSTEM_PROMPT = """You are a personal health history assistant. You answer questions \
about a patient's own confirmed medical records using ONLY the evidence provided below.

Rules:
- Never diagnose a condition, prescribe or recommend a dose, or direct treatment.
- Use only the evidence given. If it doesn't answer the question, say so plainly \
instead of guessing.
- Respond with ONLY a JSON object, no other text, in exactly this shape:
{"answer": "<your answer in plain language>", \
"claims": ["<short factual claim 1>", "<short factual claim 2>"], \
"uncertainty": "<what is uncertain or missing, or null if nothing>"}
"""


def build_messages(context: AssistantContext) -> list:
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question: {context.question}\n\n"
                f"Evidence:\n{context.to_prompt_text()}"
            ),
        },
    ]


def _parse_model_output(raw: str, intent: str, evidence_ids: list) -> Answer:
    """Models (especially small local ones) don't always return clean JSON.
    Parse defensively; never crash the user-facing response over a formatting
    slip — degrade to treating the raw text as the answer instead.
    """
    text = raw.strip()
    # Some models wrap JSON in ```json ... ``` fences despite instructions not to.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        parsed = json.loads(text)
        return Answer(
            answer=parsed.get("answer", "").strip() or "(empty response)",
            claims=parsed.get("claims") or [],
            evidence_ids=evidence_ids,
            uncertainty=parsed.get("uncertainty"),
            intent=intent,
        )
    except (json.JSONDecodeError, AttributeError):
        logger.warning("Model output was not valid JSON; falling back to raw text")
        return Answer(
            answer=raw.strip(),
            claims=[],
            evidence_ids=evidence_ids,
            uncertainty="Model response was not in the expected format.",
            intent=intent,
        )


def generate_answer(db: Session, user_id: uuid.UUID, question: str) -> Answer:
    context = build_context(db, user_id, question)

    if not context.evidence:
        return Answer(
            answer="I don't have enough confirmed information in your records to answer that.",
            claims=[],
            evidence_ids=[],
            uncertainty="No relevant evidence was found.",
            intent=context.intent,
        )

    messages = build_messages(context)
    provider = get_llm_provider()

    try:
        raw = provider.generate(messages)
    except LLMProviderError as exc:
        logger.exception("LLM provider failed")
        return Answer(
            answer="The assistant is temporarily unavailable. Please try again shortly.",
            claims=[],
            evidence_ids=[],
            uncertainty=f"Provider error: {exc}",
            intent=context.intent,
        )

    evidence_ids = [eid for e in context.evidence for eid in e.event_ids]
    return _parse_model_output(raw, context.intent, evidence_ids)
