"""Consolidate _approved/<category>/*.json into final category JSON files."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.fixtures.schema import CATEGORY_FILES, load_fixtures, parse_fixture

from .prompts import TARGET_COUNTS

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_ROOT = REPO_ROOT / "eval" / "fixtures"
APPROVED_ROOT = FIXTURES_ROOT / "_approved"


def _collect(category: str) -> list[dict]:
    cat_dir = APPROVED_ROOT / category
    if not cat_dir.exists():
        return []
    out: list[dict] = []
    for f in sorted(cat_dir.glob("*.json")):
        data = json.loads(f.read_text())
        parse_fixture(data)  # validates
        out.append(data)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail if any category is below its target count",
    )
    args = parser.parse_args()

    rows: list[tuple[str, int, int, str]] = []
    short = False
    over = False

    for category in CATEGORY_FILES:
        fixtures = _collect(category)
        fixtures.sort(key=lambda f: f["id"])
        target = sum(TARGET_COUNTS.get(category, {}).values())
        status = "ok"
        if len(fixtures) < target:
            status = "short"
            short = True
        elif len(fixtures) > target:
            status = "OVER"
            over = True
        rows.append((category, len(fixtures), target, status))

        out_file = FIXTURES_ROOT / f"{category}.json"
        out_file.write_text(json.dumps(fixtures, indent=2, ensure_ascii=False))

    print(f"{'category':28s} {'count':>6s} {'target':>7s}  status")
    for cat, count, target, status in rows:
        print(f"{cat:28s} {count:6d} {target:7d}  {status}")

    loaded = load_fixtures(FIXTURES_ROOT)
    print(f"\nload_fixtures() validated {len(loaded)} fixtures across all files")

    if over:
        return 1
    if short and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
