"""The written dataset: structure, byte-for-byte reproducibility, verification, leakage, and
a round trip through the system's own PDF parser and extractors.

The dataset is generated ONCE per test module (about a second) into a temp directory.
"""

import json
import re
import shutil
from collections import Counter

import pytest

from evaluation import dataset, generate
from evaluation.catalogs import N_DEV, N_PATIENTS, alias_table, canonical_name, patient_id
from evaluation.model import KEY_FIELD
from evaluation.writer import pdf_bytes, write_dataset
from services.document_processing.normalize import process_document
from services.document_processing.parse import extract_pdf_pages
from services.extraction.pipeline import extract_all


@pytest.fixture(scope="module")
def ds(tmp_path_factory):
    out = tmp_path_factory.mktemp("phb") / "v1"
    write_dataset(out)
    return out


@pytest.fixture(scope="module")
def truths(ds):
    files = sorted((ds / "truth").glob("p*.json"))
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# --- structure ---


def test_manifest_describes_the_whole_dataset(ds, truths):
    manifest = dataset.load_manifest(ds)
    assert manifest["dataset"] == "PersonalHealthBench" and manifest["seed"] == 2026
    assert manifest["counts"]["patients"] == N_PATIENTS == len(truths)
    assert manifest["counts"]["documents"] == 247
    assert manifest["counts"]["events"] == sum(len(t["events"]) for t in truths)
    assert manifest["split"]["dev"] == [patient_id(n) for n in range(1, N_DEV + 1)]
    assert len(manifest["split"]["test"]) == 20
    assert set(manifest["split"]["dev"]).isdisjoint(manifest["split"]["test"])
    assert {t["patient_id"]: t["split"] for t in truths} == {
        **{p: "dev" for p in manifest["split"]["dev"]},
        **{p: "test" for p in manifest["split"]["test"]}}


def test_every_patient_has_inputs_and_truth_and_nothing_else_is_written(ds, truths):
    top = {p.name for p in ds.iterdir()}
    assert top == {"manifest.json", "aliases.json", "inputs", "truth"}
    for t in truths:
        folder = ds / "inputs" / t["patient_id"]
        names = {p.name for p in folder.iterdir()}
        expected = {"index.json"}
        for doc in t["documents"]:
            stem = doc["filename"][:-4]
            expected |= {f"{stem}.pdf", f"{stem}.txt"}
        assert names == expected
    assert json.loads((ds / "aliases.json").read_text()) == alias_table()


def test_text_files_split_into_the_documents_pages(ds, truths):
    for t in truths:
        for doc in t["documents"]:
            text = (ds / "inputs" / t["patient_id"] / (doc["filename"][:-4] + ".txt")).read_text(
                encoding="utf-8")
            assert len(text.split("\f")) == doc["pages"]


# --- no leakage of the truth into the inputs ---


def test_inputs_contain_nothing_a_system_could_use_to_cheat(ds, truths):
    banned = ("archetype", "event_id", "truth", "duplicate_of", "conflicts_with",
              "degradations", "date_precision", "split", "stay_id", "no_header")
    for t in truths:
        folder = ds / "inputs" / t["patient_id"]
        index = json.loads((folder / "index.json").read_text())
        assert set(index) == {"documents"}
        for entry in index["documents"]:
            assert set(entry) == {"filename", "text_filename"}
            assert re.fullmatch(r"d\d\d\.pdf", entry["filename"])  # no type or date in a name
            assert re.fullmatch(r"d\d\d\.txt", entry["text_filename"])
        for path in folder.glob("*.txt"):
            text = path.read_text(encoding="utf-8").lower()
            assert not any(word in text for word in banned), path
            assert re.search(r"p\d{3}-[de]\d", text) is None  # no document or event id


def test_index_lists_documents_in_upload_order(ds, truths):
    for t in truths:
        index = json.loads((ds / "inputs" / t["patient_id"] / "index.json").read_text())
        listed = [e["filename"] for e in index["documents"]]
        assert listed == [d["filename"] for d in t["documents"]]


# --- truth files are internally consistent ---


def test_truth_references_resolve(truths):
    for t in truths:
        docs = {d["document_id"] for d in t["documents"]}
        events = {e["event_id"] for e in t["events"]}
        assert len(events) == len(t["events"])
        for e in t["events"]:
            assert e["source"]["document_id"] in docs
            assert set(e["conflicts_with"]) <= events
            assert e["duplicate_of"] is None  # duplicates are never separate truth events
        for d in t["documents"]:
            assert d["duplicate_of"] is None or d["duplicate_of"] in docs
        assert t["tags"]["duplicates_injected"] >= 0


# --- reproducibility ---


def test_regenerating_gives_byte_identical_files(ds, tmp_path):
    again = tmp_path / "again"
    write_dataset(again)
    first = {p.relative_to(ds).as_posix(): p.read_bytes() for p in ds.rglob("*") if p.is_file()}
    second = {p.relative_to(again).as_posix(): p.read_bytes() for p in again.rglob("*")
              if p.is_file()}
    assert first.keys() == second.keys()
    assert [k for k in first if first[k] != second[k]] == []


def test_a_different_seed_changes_the_root_hash(ds, tmp_path):
    other = write_dataset(tmp_path / "other", seed=7)
    assert other["root_hash"] != dataset.load_manifest(ds)["root_hash"]


def test_pdf_bytes_are_stable_and_carry_no_timestamp():
    pages = ["Title\nLine one", "(continued)\nLine two"]
    assert pdf_bytes(pages) == pdf_bytes(pages)
    assert b"%PDF" in pdf_bytes(pages)[:8]


def test_a_line_too_wide_for_the_page_is_an_error_not_a_silent_wrap():
    with pytest.raises(ValueError):
        pdf_bytes(["x" * 400])
    with pytest.raises(ValueError):
        pdf_bytes(["\n".join(["line"] * 200)])


# --- verification ---


def test_an_untouched_dataset_verifies(ds):
    assert dataset.verify_dataset(ds) == []


def test_verification_detects_a_changed_a_missing_and_a_stray_file(ds, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(ds, copy)
    assert dataset.verify_dataset(copy) == []

    (copy / "truth" / "p001.json").write_text("{}", encoding="utf-8")
    (copy / "inputs" / "p002" / "d01.txt").unlink()
    (copy / "inputs" / "p003" / "extra.txt").write_text("x", encoding="utf-8")
    problems = dataset.verify_dataset(copy)
    assert "changed: truth/p001.json" in problems
    assert "missing: inputs/p002/d01.txt" in problems
    assert "not in manifest: inputs/p003/extra.txt" in problems


def test_verification_detects_a_tampered_manifest(ds, tmp_path):
    copy = tmp_path / "copy"
    shutil.copytree(ds, copy)
    manifest = json.loads((copy / "manifest.json").read_text())
    manifest["root_hash"] = "0" * 64
    (copy / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert "root_hash does not match the listed file hashes" in dataset.verify_dataset(copy)


def test_verification_of_a_folder_with_no_manifest_fails(tmp_path):
    assert dataset.verify_dataset(tmp_path)[0].startswith("no manifest.json")


# --- command line ---


def test_generate_refuses_to_overwrite_without_force_and_never_deletes_foreign_folders(tmp_path):
    out = tmp_path / "data"
    assert generate.main(["--out", str(out)]) == 0
    assert dataset.main(["verify", "--dir", str(out)]) == 0
    assert generate.main(["--out", str(out)]) == 2  # exists, no --force
    assert generate.main(["--out", str(out), "--force"]) == 0

    foreign = tmp_path / "precious"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("keep me", encoding="utf-8")
    assert generate.main(["--out", str(foreign), "--force"]) == 2
    assert (foreign / "notes.txt").read_text() == "keep me"


def test_verify_command_reports_failure(tmp_path):
    out = tmp_path / "data"
    generate.main(["--out", str(out)])
    (out / "truth" / "p001.json").write_text("{}", encoding="utf-8")
    assert dataset.main(["verify", "--dir", str(out)]) == 1


# --- the PDFs really are what the text says, read by the SYSTEM'S OWN parser ---


def test_pdf_text_extracted_by_the_systems_parser_equals_the_text_file(ds, truths):
    for t in truths:
        folder = ds / "inputs" / t["patient_id"]
        for doc in t["documents"]:
            pages = extract_pdf_pages((folder / doc["filename"]).read_bytes())
            text = (folder / (doc["filename"][:-4] + ".txt")).read_text(encoding="utf-8")
            assert [_norm(p["text"]) for p in pages] == [_norm(p) for p in text.split("\f")], (
                doc["document_id"])


def test_every_source_quote_is_found_in_the_pdf_text_the_system_extracts(ds, truths):
    for t in truths:
        docs = {d["document_id"]: d for d in t["documents"]}
        for e in t["events"]:
            doc = docs[e["source"]["document_id"]]
            path = ds / "inputs" / t["patient_id"] / doc["filename"]
            pages = extract_pdf_pages(path.read_bytes())
            assert _norm(e["source"]["quote"]) in _norm(pages[e["source"]["page"] - 1]["text"])


def _key(event_type: str, data: dict) -> str:
    return canonical_name(str(data[KEY_FIELD[event_type]]))


def test_the_real_extractors_read_every_clean_line_and_the_noise_really_defeats_them(ds, truths):
    """Clean = Key: value layout, ISO date, nothing degraded. The generator's claim is that
    these are exactly what the existing extractors expect, so ALL of them must be found; and
    that the noise is meaningful, so overall recall must be far below 1."""
    clean_total = clean_found = truth_total = matched = extracted_total = 0
    for t in truths:
        extracted = Counter()
        for doc in t["documents"]:
            if doc["duplicate_of"]:
                continue
            content = (ds / "inputs" / t["patient_id"] / doc["filename"]).read_bytes()
            normalized = process_document(doc["document_id"], "application/pdf", content)
            for fact in extract_all(normalized.pages):
                extracted[(fact.event_type, fact.event_date.isoformat(),
                           _key(fact.event_type, fact.data))] += 1
        extracted_total += sum(extracted.values())
        docs = {d["document_id"]: d for d in t["documents"]}
        remaining = Counter(extracted)
        for e in t["events"]:
            meta = docs[e["source"]["document_id"]]
            key = (e["event_type"], e["event_date"], _key(e["event_type"], e["data"]))
            truth_total += 1
            hit = e["event_date"] is not None and remaining[key] > 0
            if hit:
                remaining[key] -= 1
                matched += 1
            if meta["layout"] == "key_value" and meta["date_style"] == "iso" \
                    and not e["degradations"]:
                clean_total += 1
                clean_found += hit
    assert clean_total > 100 and clean_found == clean_total
    recall = matched / truth_total
    assert 0.05 < recall < 0.5, recall  # noisy enough to matter, not impossible
    assert matched / extracted_total > 0.85  # what it does extract is mostly right
