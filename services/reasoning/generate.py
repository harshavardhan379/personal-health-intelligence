"""Structured answer generation (YGH-83) with per-claim evidence citation (YGH-84).

build_messages() turns an AssistantContext into a proper role-structured chat
prompt (system + user) — see the discussion in context_builder.py for why this
is a separate step from evidence formatting. generate_answer() is the single
entry point everything else (the API route, later M12 verification) calls.
"""

import json
import logging
import uuid

from sqlalchemy.orm import Session

from packages.schemas.answer import Answer, Claim
from services.reasoning.context_builder import AssistantContext, build_context
from services.reasoning.providers import LLMProviderError, get_llm_provider

logger = logging.getLogger("app.reasoning.generate")

_SYSTEM_PROMPT = """You are a personal health history assistant. You answer questions \
about a patient's own confirmed medical records using ONLY the evidence provided below.

Rules:
- Never diagnose a condition, prescribe or recommend a dose, or direct treatment.
- Use only the evidence given. If it doesn't answer the question, say so plainly \
instead of guessing.
- Every factual claim MUST cite the evidence number(s) (e.g. [1]) from the \
Evidence list that support it. Never state a claim with no citation.
- Respond with ONLY a JSON object, no other text, in exactly this shape:
{"answer": "<your answer in plain language, with [n] citations inline>", \
"claims": [{"text": "<short factual claim>", "citations": [1]}], \
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


def _parse_claims(claims_raw: list, context: AssistantContext) -> tuple:
    """Returns (claims, cited_evidence_ids, uncited_count). Validates citation
    indices against the actual evidence list — an out-of-range or missing
    citation is dropped, not trusted (YGH-84: "require... reference supporting
    evidence", not "trust the model's claim that it did").
    """
    n_evidence = len(context.evidence)
    claims: list[Claim] = []
    cited_evidence_ids: set = set()
    uncited_count = 0

    for raw_claim in claims_raw:
        if isinstance(raw_claim, str):
            # Model ignored the citation format entirely — keep the claim visible
            # (don't silently drop user-facing content) but it's uncited.
            claims.append(Claim(text=raw_claim, citation_indices=[]))
            uncited_count += 1
            continue

        text = str(raw_claim.get("text", "")).strip()
        if not text:
            continue

        raw_indices = raw_claim.get("citations") or []
        valid_indices = [
            i for i in raw_indices if isinstance(i, int) and 1 <= i <= n_evidence
        ]
        claims.append(Claim(text=text, citation_indices=valid_indices))

        if not valid_indices:
            uncited_count += 1
        for i in valid_indices:
            cited_evidence_ids.update(context.evidence[i - 1].event_ids)

    return claims, cited_evidence_ids, uncited_count


def _parse_model_output(raw: str, context: AssistantContext) -> Answer:
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
        # raw_decode (not json.loads) parses the first valid JSON value and
        # ignores anything trailing after it — local models sometimes emit one
        # valid JSON object plus stray trailing characters (found via live
        # testing: llama3.2:3b consistently appended an extra "}"), which
        # json.loads rejects outright as "Extra data" but raw_decode tolerates.
        parsed, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        logger.warning("Model output was not valid JSON; falling back to raw text")
        return Answer(
            answer=raw.strip(),
            claims=[],
            evidence_ids=[],
            uncertainty="Model response was not in the expected format.",
            intent=context.intent,
        )

    claims, cited_evidence_ids, uncited_count = _parse_claims(
        parsed.get("claims") or [], context
    )

    uncertainty = parsed.get("uncertainty")
    if uncited_count and not uncertainty:
        uncertainty = (
            f"{uncited_count} claim(s) in this answer were not tied to a specific "
            f"evidence citation."
        )

    return Answer(
        answer=parsed.get("answer", "").strip() or "(empty response)",
        claims=claims,
        evidence_ids=sorted(cited_evidence_ids),
        uncertainty=uncertainty,
        intent=context.intent,
    )


def generate_answer(db: Session, user_id: uuid.UUID, question: str) -> Answer:
    answer, _context = generate_answer_with_context(db, user_id, question)
    return answer


def generate_answer_with_context(
    db: Session, user_id: uuid.UUID, question: str
) -> tuple:
    """Same as generate_answer, but also returns the AssistantContext used to
    generate it — needed by services/verification/verify.py (M12), which must
    check the answer against the EXACT SAME evidence it was generated from,
    not a freshly re-retrieved (and possibly different) evidence set.
    """
    context = build_context(db, user_id, question)

    if not context.evidence:
        return Answer(
            answer="I don't have enough confirmed information in your records to answer that.",
            claims=[],
            evidence_ids=[],
            uncertainty="No relevant evidence was found.",
            intent=context.intent,
        ), context

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
        ), context

    return _parse_model_output(raw, context), context
