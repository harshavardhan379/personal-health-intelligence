"""Run the benchmark.

    python -m evaluation.run --systems fake:abstain,fake:echo --split dev --limit-patients 5
    python -m evaluation.run --list-systems
    python -m evaluation.run --systems <names> --split test --final     # the reported numbers

Development runs are diagnostics. The locked TEST split needs --final and a clean git tree; see
evaluation/runner.py. Results go to benchmarks/personal_health_bench/results/<time>-<split>/
(not committed). Exit codes: 0 done, 2 refused, 3 unknown system or nothing to run.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from evaluation.dataset import DEFAULT_DIR
from evaluation.report import primary_mode, write_results
from evaluation.runner import RunConfig, RunRefused, run_benchmark
from evaluation.systems import available, get_system

RESULTS_ROOT = Path(__file__).resolve().parent.parent / "benchmarks" / "personal_health_bench" \
    / "results"


def _summary(result) -> str:
    lines = []
    for name in sorted(result.metrics["systems"]):
        modes = result.metrics["systems"][name]
        for mode in sorted(modes):
            m = modes[mode]
            strict, abstain = (m["qa"]["overall"]["strict_correctness"],
                               m["qa"]["overall"]["abstention_correct"])
            unsafe = m["safety"]["overall"]["unsafe_response_rate"]

            def show(rate):
                if rate["n"] == 0:
                    return "n/a"
                return f"{rate['value']:.3f} ({rate['successes']}/{rate['n']})"

            marker = "*" if mode == primary_mode(modes) else " "
            lines.append(f" {marker} {name:<16} {mode:<9} strict {show(strict):<18}"
                         f" abstain {show(abstain):<16} unsafe {show(unsafe):<16}"
                         f" errors {m['errors']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.run")
    parser.add_argument("--systems", default="", help="comma-separated system names")
    parser.add_argument("--list-systems", action="store_true")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--split", choices=["dev", "test", "both"], default="dev")
    parser.add_argument("--limit-patients", type=int, default=None)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--final", action="store_true",
                        help="allow the locked test split (reported numbers; clean git tree)")
    parser.add_argument("--no-pipeline", action="store_true",
                        help="skip the real extraction/timeline metrics")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.list_systems:
        print("\n".join(available()))
        return 0
    names = [n.strip() for n in args.systems.split(",") if n.strip()]
    if not names:
        print(f"no systems given; available: {', '.join(available())}")
        return 3
    try:
        systems = [get_system(n) for n in names]
    except KeyError as error:
        print(error.args[0])
        return 3

    config = RunConfig(dataset_dir=args.dataset, split=args.split,
                       limit_patients=args.limit_patients, seed=args.seed, final=args.final,
                       score_pipeline=not args.no_pipeline)
    try:
        result = run_benchmark(config, systems)
    except RunRefused as refusal:
        print(f"refused: {refusal}")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or RESULTS_ROOT / f"{stamp}-{args.split}{'-FINAL' if args.final else ''}"
    paths = write_results(result, out)
    print(_summary(result))
    print(f"\n{len(result.rows)} scored answers; artifacts in {out}")
    for label, path in paths.items():
        print(f"  {label:<8} {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
