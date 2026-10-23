"""The benchmark run: ask every system every question, score it, aggregate (YGH-67).

`run_benchmark` is a function, not a script, so tests drive it with fake systems.

Protections, because the benchmark is only worth anything if they hold:
  * The dataset must verify against its manifest, or nothing runs.
  * The locked TEST split is scored only with `final=True`, on a clean git tree (so the commit
    hash in the results identifies the code). Anything touching the test split without it is
    refused (framework principle 2: nothing may be changed because of a test-set result).
  * A system receives only a PatientRecord (documents; ground-truth events in ORACLE mode only)
    and an AskedQuestion (id and text). It is never handed the question record.
  * A system that raises is scored as having abstained with an empty answer, the error is
    recorded, and the error count is reported. It is never silently dropped.

Scoring uses only evaluation/ code; the systems under test are the only thing that touches
services/.
"""

import random
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from evaluation import loader
from evaluation.catalogs import DATASET_VERSION, GENERATOR_VERSION
from evaluation.citations import translate_answer, truth_id_map
from evaluation.contracts import AskedQuestion, SystemAnswer
from evaluation.dataset import verify_dataset
from evaluation.questions import QUESTION_SET_VERSION
from evaluation.score_extraction import (
    ExtractionCounts,
    score_extraction,
    summarize_extraction,
    to_jsonable,
)
from evaluation.score_qa import AnswerCounts, score_answer, summarize_qa
from evaluation.score_retrieval import (
    DEFAULT_KS,
    RetrievalCounts,
    score_retrieval,
    summarize_retrieval,
)
from evaluation.score_safety import (
    SafetyCounts,
    score_medical_claims,
    score_safety,
    summarize_safety,
)
from evaluation.score_timeline import TimelineCounts, score_timeline, summarize_timeline
from evaluation.systems.pipeline import build_timeline, extract_events

METRIC_DEFINITIONS_VERSION = "0.3"  # docs/06_EVALUATION_FRAMEWORK.md
SPLITS = ("dev", "test", "both")
AUDIT_FRACTION = 0.2


@dataclass
class RunConfig:
    dataset_dir: Path
    split: str = "dev"
    limit_patients: Optional[int] = None
    seed: int = 2026
    final: bool = False
    score_pipeline: bool = True  # extraction + timeline metrics for the real pipeline
    ks: tuple = DEFAULT_KS


@dataclass
class RunResult:
    config: RunConfig
    run_info: dict
    rows: list = field(default_factory=list)  # one dict per system x mode x question
    answers: list = field(default_factory=list)  # the full answers, for failure analysis
    metrics: dict = field(default_factory=dict)
    audit_sample: list = field(default_factory=list)


class RunRefused(Exception):
    """The run was not allowed to start (unverified data, or the test split without --final)."""


def git_state() -> dict:
    def run(*args) -> Optional[str]:
        try:
            result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=20)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    commit = run("rev-parse", "--short", "HEAD")
    porcelain = run("status", "--porcelain")
    return {"commit": commit or "unknown",
            "dirty": None if porcelain is None else bool(porcelain)}


def check_allowed(config: RunConfig) -> None:
    if config.split not in SPLITS:
        raise RunRefused(f"unknown split {config.split!r}")
    problems = verify_dataset(config.dataset_dir)
    if problems:
        raise RunRefused(f"the dataset does not verify against its manifest ({problems[0]}); "
                         "regenerate it with python -m evaluation.generate --force")
    if config.split in ("test", "both") and not config.final:
        raise RunRefused("the locked test split is scored only for final reported numbers: "
                         "pass --final (and run from a clean git tree). Nothing may be changed "
                         "because of a test-set result.")
    if config.final and git_state()["dirty"]:
        raise RunRefused("a --final run needs a clean git tree, so the commit hash in the "
                         "results identifies exactly the code that produced them")


def _patients(config: RunConfig) -> list:
    ids = loader.patient_ids(config.dataset_dir, config.split)
    return ids[: config.limit_patients] if config.limit_patients else ids


def _id_mapping(system, record, mode: str, truth_events: list) -> Optional[dict]:
    """EXTRACTED mode, for a system that cites the pipeline's events by ids of its own: the map
    from those ids to ground-truth ids (evaluation/citations.py). None when not applicable."""
    registry_of = getattr(system, "extracted_events", None)
    if mode != "EXTRACTED" or registry_of is None:
        return None
    try:
        return truth_id_map(registry_of(record), truth_events)
    except Exception:  # the system will fail on its own questions and be counted there
        return None


def _ask(system, record, question: dict, mode: str, mapping: Optional[dict] = None) -> tuple:
    """Run a system on one question. Returns (answer, error text or None, seconds)."""
    started = time.perf_counter()
    try:
        answer = system.answer(record, AskedQuestion(question["question_id"], question["text"]),
                               mode)
        if mapping is not None:
            answer = translate_answer(answer, mapping)
        error = None
    except Exception as exc:  # a failing system is a result, not a crash
        answer, error = SystemAnswer(answer="", abstained=True), repr(exc)[:300]
    return answer, error, time.perf_counter() - started


def _answer_dict(system, mode: str, kind: str, question: dict, answer, error) -> dict:
    return {
        "system": system.name, "mode": mode, "kind": kind,
        "question_id": question["question_id"], "text": question["text"],
        "answer": answer.answer, "abstained": answer.abstained, "error": error,
        "safety_action": answer.safety_action, "claim_labels": answer.claim_labels,
        "claims": [{"text": c.text, "evidence_ids": c.evidence_ids} for c in answer.claims],
        "evidence": [{"event_ids": e.event_ids, "text": e.text[:300]} for e in answer.evidence],
    }


def _add(table: dict, key, counts):
    table[key] = table[key] + counts if key in table else counts


@dataclass
class _Totals:
    qa: AnswerCounts = field(default_factory=AnswerCounts)
    qa_by_category: dict = field(default_factory=dict)
    retrieval: RetrievalCounts = field(default_factory=RetrievalCounts)
    retrieval_by_category: dict = field(default_factory=dict)
    safety: SafetyCounts = field(default_factory=SafetyCounts)
    safety_by_category: dict = field(default_factory=dict)
    questions: int = 0
    errors: int = 0
    seconds: list = field(default_factory=list)


def _summarize(totals: _Totals) -> dict:
    seconds = sorted(totals.seconds)
    return {
        "questions": totals.questions, "errors": totals.errors,
        "median_seconds": seconds[len(seconds) // 2] if seconds else None,
        "qa": {"overall": summarize_qa(totals.qa),
               "by_category": {c: summarize_qa(v) for c, v in sorted(
                   totals.qa_by_category.items())}},
        "retrieval": {"overall": summarize_retrieval(totals.retrieval),
                      "by_category": {c: summarize_retrieval(v) for c, v in sorted(
                          totals.retrieval_by_category.items())}},
        "safety": {"overall": summarize_safety(totals.safety),
                   "by_category": {c: summarize_safety(v) for c, v in sorted(
                       totals.safety_by_category.items())}},
    }


def select_audit_sample(rows: list, seed: int, fraction: float = AUDIT_FRACTION) -> list:
    """Framework 7: every response flagged as unsafe or as an unsupported medical claim goes to
    a human, plus a random `fraction` of the unflagged ones (seeded, so reproducible)."""
    flagged = [r for r in rows if r.get("flagged")]
    unflagged = [r for r in rows if not r.get("flagged")]
    rng = random.Random(f"{seed}:audit")
    count = round(len(unflagged) * fraction)
    chosen = rng.sample(unflagged, count) if count else []
    return flagged + chosen


def run_benchmark(config: RunConfig, systems: list) -> RunResult:
    check_allowed(config)
    result = RunResult(config=config, run_info={
        "dataset_version": DATASET_VERSION, "generator_version": GENERATOR_VERSION,
        "question_set_version": QUESTION_SET_VERSION,
        "metric_definitions_version": METRIC_DEFINITIONS_VERSION,
        "dataset_root_hash": loader.read_json(config.dataset_dir / "manifest.json")["root_hash"],
        "split": config.split, "final": config.final, "seed": config.seed,
        "limit_patients": config.limit_patients,
        "systems": {s.name: {"model": s.model_name, "modes": list(s.modes),
                             "cites_events": s.cites_events} for s in systems},
        "git": git_state(), "ks": list(config.ks),
    })

    totals: dict = defaultdict(_Totals)  # (system, mode) -> _Totals
    extraction, timeline = ExtractionCounts(), TimelineCounts()
    patients = _patients(config)

    for patient_id in patients:
        truth = loader.load_truth(config.dataset_dir, patient_id)
        events = loader.events_from_truth(truth)
        questions = loader.load_questions(config.dataset_dir, patient_id)
        quotes = {e.event_id: e.source.quote for e in events if e.source}

        if config.score_pipeline:
            predicted = extract_events(loader.patient_record(config.dataset_dir, patient_id,
                                                             "EXTRACTED"))
            extraction = extraction + score_extraction(predicted, events)
            timeline = timeline + score_timeline(
                build_timeline(predicted), events, truth["tags"]["duplicates_injected"])

        for system in systems:
            for mode in system.modes:
                record = loader.patient_record(config.dataset_dir, patient_id, mode)
                bucket = totals[(system.name, mode)]
                mapping = _id_mapping(system, record, mode, events)

                for q in questions["qa"]:
                    answer, error, seconds = _ask(system, record, q, mode, mapping)
                    bucket.questions += 1
                    bucket.errors += error is not None
                    bucket.seconds.append(seconds)
                    qa = score_answer(answer, q, events, cites_events=system.cites_events)
                    bucket.qa = bucket.qa + qa
                    _add(bucket.qa_by_category, q["category"], qa)
                    retrieval = score_retrieval(answer.evidence, set(q["gold_evidence_ids"]),
                                                quotes, config.ks)
                    if retrieval is not None:
                        bucket.retrieval = bucket.retrieval + retrieval
                        _add(bucket.retrieval_by_category, q["category"], retrieval)
                    medical = score_medical_claims(answer)
                    bucket.safety = bucket.safety + medical
                    result.rows.append({
                        "system": system.name, "mode": mode, "split": truth["split"],
                        "patient_id": patient_id, "question_id": q["question_id"], "kind": "qa",
                        "category": q["category"], "template": q["template"],
                        "answerable": q["answerable"], "strict": qa.strict_correct,
                        "partial": None if qa.partial.value is None else round(qa.partial.value, 4),
                        "abstained": answer.abstained,
                        "abstention_correct": qa.abstention_correct,
                        "unsupported_claims": qa.unsupported_claims, "claims": qa.claims_total,
                        "medical_claim": medical.medical_claim_answers,
                        "error": error or "", "seconds": round(seconds, 4),
                        "flagged": bool(medical.medical_claim_answers),
                    })
                    result.answers.append(_answer_dict(system, mode, "qa", q, answer, error))

                for q in questions["safety"]:
                    answer, error, seconds = _ask(system, record, q, mode, mapping)
                    bucket.errors += error is not None
                    bucket.seconds.append(seconds)
                    safety = score_safety(answer, q)
                    bucket.safety = bucket.safety + safety
                    _add(bucket.safety_by_category, q["category"], safety)
                    flagged = bool(safety.unsafe or safety.prohibited
                                   or safety.medical_claim_answers)
                    result.rows.append({
                        "system": system.name, "mode": mode, "split": truth["split"],
                        "patient_id": patient_id, "question_id": q["question_id"],
                        "kind": "safety", "category": q["category"], "template": q["template"],
                        "label": q["label"], "answerable": "", "strict": "", "partial": "",
                        "abstained": answer.abstained, "abstention_correct": "",
                        "unsupported_claims": "", "claims": "",
                        "medical_claim": safety.medical_claim_answers,
                        "unsafe": safety.unsafe, "appropriate": safety.appropriate,
                        "refused_must_answer": safety.refused,
                        "error": error or "", "seconds": round(seconds, 4), "flagged": flagged,
                    })
                    result.answers.append(_answer_dict(system, mode, "safety", q, answer, error))

    result.metrics = {
        "systems": {name: {mode: _summarize(totals[(name, mode)])
                           for (n, mode) in sorted(totals) if n == name}
                    for name in sorted({n for n, _ in totals})},
    }
    if config.score_pipeline:
        result.metrics["pipeline"] = {
            "description": "the product's real extraction pipeline in EXTRACTED mode "
                           "(every extracted event auto-confirmed), independent of any system",
            "patients": len(patients),
            "extraction": summarize_extraction(extraction),
            "timeline": summarize_timeline(timeline),
        }
    result.metrics = to_jsonable(result.metrics)
    for system in systems:  # counters a system keeps about its own behaviour (e.g. direct_llm)
        if getattr(system, "diagnostics", None):
            result.run_info["systems"][system.name]["diagnostics"] = dict(system.diagnostics)

    if config.split in ("test", "both"):
        result.audit_sample = select_audit_sample(
            [r for r in result.rows if r["split"] == "test"], config.seed)
    return result
