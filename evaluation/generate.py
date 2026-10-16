"""Generate the PersonalHealthBench dataset.

    python -m evaluation.generate [--out DIR] [--seed N] [--force]

The data is NOT committed to git: it is regenerated on demand and is byte-identical for the
same seed (see evaluation/writer.py). Afterwards, check it with
`python -m evaluation.dataset verify`.
"""

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

from evaluation.catalogs import DEFAULT_SEED
from evaluation.dataset import DEFAULT_DIR
from evaluation.writer import write_dataset


def _clear_readonly_and_retry(func, path, _excinfo) -> None:
    """Windows refuses to remove a directory (or file) carrying the ReadOnly attribute, and
    the generated folders can end up with it. Clear the attribute and try that step again."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_dataset(out: Path) -> None:
    handler = {"onexc": _clear_readonly_and_retry} if sys.version_info >= (3, 12) else {
        "onerror": _clear_readonly_and_retry}
    shutil.rmtree(out, **handler)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.generate")
    parser.add_argument("--out", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true",
                        help="replace an existing generated dataset in --out")
    args = parser.parse_args(argv)

    out: Path = args.out
    if out.exists() and any(out.iterdir()):
        if not args.force:
            print(f"{out} already exists; use --force to replace it")
            return 2
        # Only ever delete something that is recognisably a generated dataset.
        if not (out / "manifest.json").is_file():
            print(f"refusing to delete {out}: it has no manifest.json, so it is not a dataset")
            return 2
        try:
            remove_dataset(out)
        except OSError as error:
            print(f"could not remove {out}: {error}. Close any program using files in it "
                  "(an editor, a PDF viewer, a sync or antivirus scan) and try again.")
            return 3

    manifest = write_dataset(out, args.seed)
    counts = manifest["counts"]
    print(f"wrote {counts['patients']} patients, {counts['documents']} documents "
          f"({counts['reuploads']} re-uploads), {counts['events']} ground-truth events to {out}")
    q = manifest["questions"]
    print(f"{q['qa']} QA questions and {q['safety']} safety questions "
          f"(question set v{manifest['question_set_version']})")
    print(f"dataset v{manifest['dataset_version']} seed {manifest['seed']} "
          f"root hash {manifest['root_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
