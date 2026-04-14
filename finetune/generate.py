"""Generate SFT / DPO training datasets from consolidated eval fixtures.

Usage::

    python -m finetune.generate                  # consolidate + both formats
    python -m finetune.generate --format sft     # SFT only
    python -m finetune.generate --no-consolidate # skip consolidation step
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from eval.fixtures.schema import Fixture, load_fixtures
from finetune.format import fixture_to_dpo, fixture_to_sft

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = REPO_ROOT / "eval" / "fixtures"
DATASETS_DIR = REPO_ROOT / "finetune" / "datasets"


def _consolidate() -> int:
    """Run the fixture consolidation pipeline."""
    print("Running fixture consolidation ...")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_eval_fixtures.py"), "consolidate"],
        cwd=str(REPO_ROOT),
    )
    return result.returncode


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=["sft", "dpo", "both"],
        default="both",
        help="Which dataset format(s) to generate (default: both)",
    )
    parser.add_argument(
        "--no-consolidate",
        action="store_true",
        help="Skip fixture consolidation (assumes already done)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DATASETS_DIR,
        help=f"Output directory (default: {DATASETS_DIR})",
    )
    args = parser.parse_args()

    # Step 1: consolidate
    if not args.no_consolidate:
        rc = _consolidate()
        if rc != 0:
            print("Consolidation failed — aborting.", file=sys.stderr)
            return 1
        print()

    # Step 2: load fixtures
    fixtures = load_fixtures(FIXTURES_ROOT)
    standard = [f for f in fixtures if isinstance(f, Fixture)]
    skipped = len(fixtures) - len(standard)

    print(f"Loaded {len(fixtures)} fixtures ({len(standard)} standard, {skipped} cold_start skipped)")

    if not standard:
        print("No standard fixtures to convert.", file=sys.stderr)
        return 1

    # Step 3: convert
    sft_records: list[dict] = []
    dpo_records: list[dict] = []
    band_counts: Counter[str] = Counter()

    for fixture in standard:
        band_counts[fixture.metadata.cefr_band] += 1

        if args.format in ("sft", "both"):
            sft_records.append(fixture_to_sft(fixture))

        if args.format in ("dpo", "both"):
            dpo_records.extend(fixture_to_dpo(fixture))

    # Step 4: write
    if sft_records:
        sft_path = args.output_dir / "sft_train.jsonl"
        _write_jsonl(sft_path, sft_records)
        print(f"Wrote {len(sft_records)} SFT examples → {sft_path}")

    if dpo_records:
        dpo_path = args.output_dir / "dpo_train.jsonl"
        _write_jsonl(dpo_path, dpo_records)
        print(f"Wrote {len(dpo_records)} DPO pairs → {dpo_path}")

    # Summary
    print(f"\nCEFR distribution:")
    for band in ["A1", "A2", "B1", "B2", "C1"]:
        print(f"  {band}: {band_counts.get(band, 0)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
