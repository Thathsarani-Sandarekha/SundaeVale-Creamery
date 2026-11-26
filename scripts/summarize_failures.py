"""Summarize existing invoice evaluation runs without API calls.

Usage: python scripts/summarize_failures.py evaluation-results/v2/*.json
"""
from __future__ import annotations
import json
import sys
from collections import Counter
from pathlib import Path


def summarize(path: Path) -> None:
    report = json.loads(path.read_text(encoding='utf-8'))
    skipped = report.get('skipped', [])
    reason_counts = Counter(row.get('reason', '<unknown>') for row in skipped)
    total = report.get('total_fixtures', 0)
    attempted = total - reason_counts.get('scan_excluded_add_--include-scans', 0)
    scored = report.get('scored_count', 0)
    pct = 100 * scored / attempted if attempted else 0
    print(f'\n{path}:')
    print(f'  {report.get("mode", "unknown")}: {scored}/{attempted} attempted scored ({pct:.1f}%); {total} in manifest')
    print(f'  field_accuracy_on_successes={report.get("field_accuracy")}  (NOT full-set accuracy)')
    if not skipped:
        print('  skipped reasons: none')
    for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
        print(f'  skipped: {count} x {reason}')


def main() -> int:
    if len(sys.argv) < 2:
        print('Usage: python scripts/summarize_failures.py <report.json> [another-report.json...]')
        return 2
    for filename in sys.argv[1:]:
        summarize(Path(filename))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
