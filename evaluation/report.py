"""Writing a run's artifacts (framework section 8, "Result artifacts").

    metrics.json        run info + every rate as {successes, n, value, ci_low, ci_high}
    results.csv         one row per system x mode x question
    answers.jsonl       the full answers, for failure analysis
    audit_sample.csv    (test-split runs) the responses a human must review, with blank verdict
    report.html         a self-contained page: no scripts, nothing fetched

The report is honest by construction: it states which split it came from, shows every rate with
its counts and 95% interval, calls something a difference only when intervals do not overlap
(framework principle 4), and lists failure cases as well as averages (principle 5).
"""

import csv
import html
import json
from pathlib import Path

ROW_COLUMNS = [
    "system", "mode", "split", "patient_id", "question_id", "kind", "category", "template",
    "label", "answerable", "strict", "partial", "abstained", "abstention_correct",
    "unsupported_claims", "claims", "medical_claim", "unsafe", "appropriate",
    "refused_must_answer", "flagged", "error", "seconds",
]

# (label, path into one system+mode's metrics). Higher is better unless the label says lower.
HEADLINE = [
    ("Strict correctness", ("qa", "overall", "strict_correctness"), True),
    ("Partial credit", ("qa", "overall", "partial_credit"), True),
    ("Correct abstention", ("qa", "overall", "abstention_correct"), True),
    ("Over-abstention (lower)", ("qa", "overall", "over_abstention_rate"), False),
    ("Confident wrong (lower)", ("qa", "overall", "confident_wrong_rate"), False),
    ("Evidence precision", ("qa", "overall", "evidence_precision"), True),
    ("Evidence recall", ("qa", "overall", "evidence_recall"), True),
    ("Citation correctness", ("qa", "overall", "citation_correctness"), True),
    ("Unsupported claims (lower)", ("qa", "overall", "unsupported_claim_rate"), False),
    ("Recall@5", ("retrieval", "overall", "recall_at_k", "5"), True),
    ("MRR", ("retrieval", "overall", "mrr"), True),
    ("Unsafe responses (lower)", ("safety", "overall", "unsafe_response_rate"), False),
    ("Appropriate abstention", ("safety", "overall", "appropriate_abstention_rate"), True),
    ("Over-refusal (lower)", ("safety", "overall", "over_refusal_rate"), False),
    ("Unsupported medical claims (lower)",
     ("safety", "overall", "unsupported_medical_claim_rate"), False),
]


def _lookup(tree: dict, key):
    """Metrics keyed by an integer (Recall@K) are int in memory and str after a JSON round trip."""
    candidates = [key, str(key)]
    if str(key).isdigit():
        candidates.append(int(key))
    for candidate in candidates:
        if candidate in tree:
            return tree[candidate]
    return None


def dig(tree, path):
    """Walk a metrics dict by `path`; None as soon as a step is missing."""
    for key in path:
        if not isinstance(tree, dict):
            return None
        tree = _lookup(tree, key)
        if tree is None:
            return None
    return tree


def cell(metric) -> str:
    if not isinstance(metric, dict):
        return "n/a"
    if "mean" in metric:
        if metric["n"] == 0 or metric["mean"] is None:
            return "n/a"
        return (f"{metric['mean']:.3f} <small>[{metric['ci_low']:.2f}-{metric['ci_high']:.2f}]"
                f" (n={metric['n']})</small>")
    if metric.get("n", 0) == 0 or metric.get("value") is None:
        return "n/a"
    return (f"{metric['value']:.3f} <small>[{metric['ci_low']:.2f}-{metric['ci_high']:.2f}]"
            f" ({metric['successes']}/{metric['n']})</small>")


def _value(metric):
    if not isinstance(metric, dict) or metric.get("n", 0) == 0:
        return None
    return metric.get("value", metric.get("mean"))


def intervals_overlap(a: dict, b: dict) -> bool:
    return not (a["ci_high"] < b["ci_low"] or b["ci_high"] < a["ci_low"])


def primary_mode(modes: dict) -> str:
    """The mode a system is compared in: ORACLE if it has one, else its only mode."""
    return "ORACLE" if "ORACLE" in modes else sorted(modes)[0]


def significant_differences(metrics: dict) -> list:
    """Pairs of systems that differ on a headline metric: intervals do not overlap."""
    systems = metrics["systems"]
    found = []
    names = sorted(systems)
    for label, path, higher_is_better in HEADLINE:
        for i, first in enumerate(names):
            for second in names[i + 1:]:
                a = dig(systems[first][primary_mode(systems[first])], path)
                b = dig(systems[second][primary_mode(systems[second])], path)
                if _value(a) is None or _value(b) is None or intervals_overlap(a, b):
                    continue
                better, worse = (first, second) if (_value(a) > _value(b)) == higher_is_better \
                    else (second, first)
                found.append(f"{better} is better than {worse} on {label.replace(' (lower)', '')}")
    return found


# --- files ------------------------------------------------------------------------------------


def _write_csv(path: Path, rows: list, columns: list) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def failure_cases(result, limit: int = 8) -> dict:
    """First `limit` failures per (system, mode): wrong or missing answers, unsafe responses."""
    answers = {(a["system"], a["mode"], a["question_id"]): a for a in result.answers}
    out: dict = {}
    for row in result.rows:
        failed = ((row["kind"] == "qa" and row["answerable"] and not row["strict"])
                  or (row["kind"] == "qa" and not row["answerable"]
                      and not row["abstention_correct"])
                  or (row["kind"] == "safety" and row.get("unsafe")))
        if not failed:
            continue
        bucket = out.setdefault((row["system"], row["mode"]), [])
        if len(bucket) < limit:
            answer = answers[(row["system"], row["mode"], row["question_id"])]
            bucket.append((row, answer))
    return out


def _table(headers: list, rows: list) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


CSS = """
body{font:15px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:1200px;padding:0 1rem;
color:#1c1c1c}
h1{margin-bottom:.2rem} h2{margin-top:2.2rem;border-bottom:1px solid #ccc;padding-bottom:.2rem}
table{border-collapse:collapse;margin:.6rem 0;font-size:13px} th,td{border:1px solid #ccc;
padding:4px 8px;text-align:left;vertical-align:top} th{background:#f2f2f2} small{color:#555}
.banner{padding:.7rem 1rem;border-radius:6px;margin:1rem 0}
.final{background:#fde8e8;border:1px solid #c0392b}
.dev{background:#fff7e0;border:1px solid #d4a017}
code{background:#f2f2f2;padding:1px 4px} .answer{max-width:520px;overflow-wrap:anywhere}
"""


def render_html(result) -> str:
    info, metrics = result.run_info, result.metrics
    esc = html.escape
    parts = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
             f"<title>PersonalHealthBench results</title><style>{CSS}</style></head><body>"]
    parts.append("<h1>PersonalHealthBench results</h1>")
    if info["final"]:
        parts.append("<div class='banner final'><b>FINAL RUN on the locked test split.</b> These "
                     "are reportable numbers. Nothing may be changed because of them; if a bug is "
                     "found, fix it and report the before and after.</div>")
    else:
        split = esc(info["split"])
        parts.append(f"<div class='banner dev'><b>Development run ({split} split).</b> "
                     "A diagnostic, not a reported number: anything tuned on these questions "
                     "cannot be used to claim generalisation.</div>")

    systems = info["systems"]
    git = info["git"]
    parts.append("<h2>Run</h2>" + _table(["field", "value"], [
        ["dataset", f"v{esc(info['dataset_version'])} (generator {esc(info['generator_version'])},"
                    f" question set v{esc(info['question_set_version'])})"],
        ["dataset root hash", f"<code>{esc(info['dataset_root_hash'])}</code>"],
        ["metric definitions", f"framework v{esc(info['metric_definitions_version'])}"],
        ["split / patients", f"{esc(info['split'])} / limit {esc(str(info['limit_patients']))}"],
        ["seed", esc(str(info["seed"]))],
        ["code", f"commit <code>{esc(str(git['commit']))}</code>"
                 f"{' (UNCOMMITTED CHANGES)' if git['dirty'] else ''}"],
        ["systems", "<br>".join(
            f"{esc(n)}: model {esc(s['model'])}, modes {esc(', '.join(s['modes']))}"
            for n, s in sorted(systems.items()))],
    ]))

    rows = []
    for name in sorted(metrics["systems"]):
        for mode in sorted(metrics["systems"][name]):
            m = metrics["systems"][name][mode]
            rows.append([esc(name), esc(mode)] + [cell(dig(m, p)) for _, p, _ in HEADLINE]
                        + [str(m["errors"]),
                           "n/a" if m["median_seconds"] is None else f"{m['median_seconds']:.2f}"])
    parts.append("<h2>Headline metrics</h2><p>Each rate shows its value, 95% interval and counts. "
                 "Strict correctness is the headline answer metric.</p>" + _table(
                     ["system", "mode"] + [label for label, _, _ in HEADLINE]
                     + ["errors", "median s"], rows))

    diffs = significant_differences(metrics)
    parts.append("<h2>Differences</h2><p>Reported only where the 95% intervals do not overlap."
                 "</p>" + ("<ul>" + "".join(f"<li>{esc(d)}</li>" for d in diffs) + "</ul>"
                           if diffs else "<p>No headline metric separates any two systems.</p>"))

    cat_rows = []
    for name in sorted(metrics["systems"]):
        for mode in sorted(metrics["systems"][name]):
            for category, c in metrics["systems"][name][mode]["qa"]["by_category"].items():
                cat_rows.append([esc(name), esc(mode), esc(category), str(c["answerable"]),
                                 cell(c["strict_correctness"]), cell(c["abstention_correct"])])
    parts.append("<h2>By question category</h2>" + _table(
        ["system", "mode", "category", "answerable", "strict correctness",
         "correct abstention"], cat_rows))

    if "pipeline" in metrics:
        p = metrics["pipeline"]
        e, t = p["extraction"], p["timeline"]
        parts.append(f"<h2>The real extraction pipeline</h2><p>{esc(p['description'])} "
                     f"({p['patients']} patients). Low recall is expected: the extractors are a "
                     "regex placeholder facing noisy documents (framework section 3).</p>"
                     + _table(["metric", "value"], [
                         ["precision", cell(e["overall"]["precision"])],
                         ["recall", cell(e["overall"]["recall"])],
                         ["recall, dated events only", cell(e["recall_dated_only"])],
                         ["date recall", cell(e["date_recall"])],
                         ["numeric / unit / dose accuracy", " | ".join(cell(e[k]) for k in (
                             "numeric_accuracy", "unit_accuracy", "dose_accuracy"))],
                         ["undated events found / invented-date rate",
                          f"{cell(e['undated_recall'])} | {cell(e['invented_date_rate'])}"],
                         ["timeline ordering accuracy", cell(t["ordering_accuracy"])],
                         ["duplicate removal / residual duplicates",
                          f"{cell(t['duplicate_removal_rate'])} | "
                          f"{cell(t['residual_duplicate_rate'])}"],
                     ]))

    parts.append("<h2>Failure cases</h2><p>The first few per system, not a random sample.</p>")
    for (name, mode), cases in sorted(failure_cases(result).items()):
        parts.append(f"<h3>{esc(name)} ({esc(mode)})</h3>" + _table(
            ["question", "category", "answer (start)", "abstained"],
            [[esc(a["text"]), esc(r["category"]),
              f"<div class='answer'>{esc(a['answer'][:240])}</div>", str(a["abstained"])]
             for r, a in cases]))

    parts.append("<h2>Limitations</h2><p>Synthetic data written by the same author as the "
                 "systems under test; small samples (wide intervals); heuristic fact matching "
                 "and safety checkers, hence the human audit; an <code>abstained</code> flag whose "
                 "origin differs by system; no clinical validation. See framework section 9.</p>")
    parts.append("</body></html>")
    return "".join(parts)


def write_results(result, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "metrics": out_dir / "metrics.json", "results": out_dir / "results.csv",
        "answers": out_dir / "answers.jsonl", "report": out_dir / "report.html",
    }
    paths["metrics"].write_text(
        json.dumps({"run_info": result.run_info, "metrics": result.metrics}, sort_keys=True,
                   indent=2) + "\n", encoding="utf-8")
    _write_csv(paths["results"], result.rows, ROW_COLUMNS)
    with paths["answers"].open("w", encoding="utf-8", newline="") as handle:
        for answer in result.answers:
            handle.write(json.dumps(answer, sort_keys=True) + "\n")
    paths["report"].write_text(render_html(result), encoding="utf-8")
    if result.audit_sample:
        paths["audit"] = out_dir / "audit_sample.csv"
        rows = [{**r, "reviewer_verdict": "", "reviewer_notes": ""} for r in result.audit_sample]
        _write_csv(paths["audit"], rows, ROW_COLUMNS + ["reviewer_verdict", "reviewer_notes"])
    return paths
