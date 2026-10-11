"""Generate the PersonalHealthBench dataset.

    python -m evaluation.generate [--out DIR] [--seed N] [--force]

The data is NOT committed to git: it is regenerated on demand and is byte-identical for the
same seed (see evaluation/writer.py). Afterwards, check it with
`python -m evaluation.dataset verify`.
"""

import argparse
import shutil
from pathlib import Path

from evaluation.catalogs import DEFAULT_SEED
from evaluation.dataset import DEFAULT_DIR
from evaluation.writer import write_dataset


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
        shutil.rmtree(out)

    manifest = write_dataset(out, args.seed)
    counts = manifest["counts"]
    print(f"wrote {counts['patients']} patients, {counts['documents']} documents "
          f"({counts['reuploads']} re-uploads), {counts['events']} ground-truth events to {out}")
    print(f"dataset v{manifest['dataset_version']} seed {manifest['seed']} "
          f"root hash {manifest['root_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
