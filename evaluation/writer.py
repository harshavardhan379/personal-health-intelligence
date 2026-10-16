"""Writes the generated dataset to disk (dataset spec, "Dataset layout").

    <out>/manifest.json         versions, seed, counts, sha256 of every file, one root hash
    <out>/aliases.json          the name alias table (framework decision 8)
    <out>/inputs/pNNN/          WHAT A SYSTEM MAY SEE: index.json, dNN.pdf, dNN.txt
    <out>/truth/pNNN.json       WHAT ONLY THE SCORER MAY SEE
    <out>/questions/pNNN.json   the question set (QA + safety), generated from truth/ alone

Byte-for-byte reproducibility (the data is regenerated on demand, not committed):
  * every file is written as bytes (no newline translation), JSON with sorted keys;
  * PDFs use ReportLab invariant=1 (no timestamp, no random document id) and no page
    compression (zlib output can differ between zlib builds). The bytes are identical for
    the pinned reportlab version in requirements.txt; another version may differ, which
    `python -m evaluation.dataset verify` will report.
"""

import hashlib
import io
import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from evaluation.catalogs import (
    DATASET_VERSION,
    DEFAULT_SEED,
    GENERATOR_VERSION,
    N_DEV,
    N_PATIENTS,
    NOISE,
    alias_table,
    patient_id,
)
from evaluation.planner import plan_patient
from evaluation.questions import QUESTION_SET_VERSION, generate_questions
from evaluation.render import RenderedPatient, render_patient

FONT, FONT_SIZE, LEADING = "Helvetica", 9, 12
LEFT_MARGIN, TOP = 54, 740
MAX_WIDTH = letter[0] - 2 * LEFT_MARGIN
MAX_LINES_PER_PAGE = int((TOP - 50) / LEADING)


def dumps(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def pdf_bytes(pages: list) -> bytes:
    """One PDF page per text page, one drawn line per text line. A line that would not fit
    is an error, never silently wrapped: a wrapped line could not be found verbatim in the
    extracted text, which is how source quotes are checked."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter, invariant=1, pageCompression=0)
    for text in pages:
        lines = text.split("\n")
        if len(lines) > MAX_LINES_PER_PAGE:
            raise ValueError(f"page has {len(lines)} lines, at most {MAX_LINES_PER_PAGE} fit")
        pdf.setFont(FONT, FONT_SIZE)
        y = TOP
        for line in lines:
            if stringWidth(line, FONT, FONT_SIZE) > MAX_WIDTH:
                raise ValueError(f"line too wide for the page: {line!r}")
            pdf.drawString(LEFT_MARGIN, y, line)
            y -= LEADING
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def root_hash(files: dict) -> str:
    """One hash over every file: sha256 of 'path:sha256' lines, sorted by path."""
    lines = "\n".join(f"{path}:{files[path]}" for path in sorted(files))
    return sha256_hex(lines.encode("utf-8"))


def _write_patient(out: Path, rendered: RenderedPatient, files: dict, seed: int) -> dict:
    """Writes one patient's files and returns that patient's questions (for the counts)."""
    pid = rendered.truth.patient_id

    def put(relative: str, data: bytes) -> None:
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[relative] = sha256_hex(data)

    index = []
    for doc in rendered.documents:  # already in upload order
        stem = doc.meta.filename[:-4]
        put(f"inputs/{pid}/{stem}.pdf", pdf_bytes(doc.pages))
        # Pages separated by a form-feed: what the Naive RAG baseline indexes.
        put(f"inputs/{pid}/{stem}.txt", "\f".join(doc.pages).encode("utf-8"))
        index.append({"filename": f"{stem}.pdf", "text_filename": f"{stem}.txt"})
    # Only what a real user would supply: filenames, in upload order. Never a type or a date.
    put(f"inputs/{pid}/index.json", dumps({"documents": index}))
    truth = rendered.truth.to_dict()
    put(f"truth/{pid}.json", dumps(truth))
    questions = generate_questions(truth, seed)
    put(f"questions/{pid}.json", dumps(questions))
    return questions


def write_dataset(out: Path, seed: int = DEFAULT_SEED) -> dict:
    """Generates all patients into `out` (which must not already hold a dataset) and returns
    the manifest it wrote."""
    out.mkdir(parents=True, exist_ok=True)
    files: dict = {}
    counts = {"patients": 0, "documents": 0, "reuploads": 0, "events": 0}
    qa_by_category: dict = {}
    safety_by_label: dict = {}
    for n in range(1, N_PATIENTS + 1):
        rendered = render_patient(plan_patient(n, seed), seed)
        questions = _write_patient(out, rendered, files, seed)
        for q in questions["qa"]:
            qa_by_category[q["category"]] = qa_by_category.get(q["category"], 0) + 1
        for q in questions["safety"]:
            safety_by_label[q["label"]] = safety_by_label.get(q["label"], 0) + 1
        counts["patients"] += 1
        counts["documents"] += len(rendered.documents)
        counts["reuploads"] += sum(1 for d in rendered.documents if d.meta.duplicate_of)
        counts["events"] += len(rendered.truth.events)

    aliases = dumps(alias_table())
    (out / "aliases.json").write_bytes(aliases)
    files["aliases.json"] = sha256_hex(aliases)

    manifest = {
        "dataset": "PersonalHealthBench",
        "dataset_version": DATASET_VERSION,
        "generator_version": GENERATOR_VERSION,
        "noise_profile_version": NOISE.version,
        "question_set_version": QUESTION_SET_VERSION,
        "seed": seed,
        "counts": counts,
        "questions": {
            "qa": sum(qa_by_category.values()),
            "qa_by_category": dict(sorted(qa_by_category.items())),
            "safety": sum(safety_by_label.values()),
            "safety_by_label": dict(sorted(safety_by_label.items())),
        },
        "split": {
            "dev": [patient_id(n) for n in range(1, N_DEV + 1)],
            "test": [patient_id(n) for n in range(N_DEV + 1, N_PATIENTS + 1)],
        },
        "files": {path: files[path] for path in sorted(files)},
        "root_hash": root_hash(files),
    }
    (out / "manifest.json").write_bytes(dumps(manifest))
    return manifest
