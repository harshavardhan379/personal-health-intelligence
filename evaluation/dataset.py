"""Verifying a generated dataset against its manifest.

    python -m evaluation.dataset verify [--dir DIR]

Exit code 0 when every file matches the manifest and the root hash agrees, 1 otherwise.
The benchmark runner refuses to run on a dataset that does not verify, and every results
file records the root hash, so two result sets can only be compared if they were measured
on the same data.
"""

import argparse
import json
from pathlib import Path

from evaluation.catalogs import DATASET_VERSION
from evaluation.writer import root_hash, sha256_hex

DEFAULT_DIR = (
    Path(__file__).resolve().parent.parent / "benchmarks" / "personal_health_bench"
    / "data" / f"v{DATASET_VERSION}"
)
# Everything the generator writes lives under these; anything else in the folder is foreign.
_OWNED_PREFIXES = ("inputs/", "truth/")
_OWNED_FILES = ("aliases.json",)


def load_manifest(directory: Path) -> dict:
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def verify_dataset(directory: Path) -> list:
    """Returns a list of problems; empty means the dataset is exactly what the manifest says."""
    if not (directory / "manifest.json").is_file():
        return [f"no manifest.json in {directory}"]
    manifest = load_manifest(directory)
    listed = manifest["files"]
    problems = []

    for relative, expected in listed.items():
        path = directory / relative
        if not path.is_file():
            problems.append(f"missing: {relative}")
        elif sha256_hex(path.read_bytes()) != expected:
            problems.append(f"changed: {relative}")

    on_disk = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    for relative in sorted(on_disk - set(listed)):
        if relative.startswith(_OWNED_PREFIXES) or relative in _OWNED_FILES:
            problems.append(f"not in manifest: {relative}")

    if root_hash(listed) != manifest["root_hash"]:
        problems.append("root_hash does not match the listed file hashes")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.dataset")
    parser.add_argument("command", choices=["verify"])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)

    problems = verify_dataset(args.dir)
    if problems:
        print(f"FAILED: {len(problems)} problem(s) in {args.dir}")
        for problem in problems[:20]:
            print(f"  {problem}")
        return 1
    manifest = load_manifest(args.dir)
    print(f"OK: {len(manifest['files'])} files, root hash {manifest['root_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
