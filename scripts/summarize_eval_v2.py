"""Print coverage-aware category summaries from existing SundaeVale evaluation JSONs.

Usage:
    python scripts/summarize_eval_v2.py evaluation-results/v2/rules-evaluation.json
    python scripts/summarize_eval_v2.py evaluation-results/v2/*-evaluation.json

No evaluation calls are made; no network or file mutations occur.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

DEFAULT_MANIFEST = Path("samples/evaluation_v2/ground_truth.json")


def summarize(manifest: Path, result_files: list[Path]) -> None:
    manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
    labels = {r["file"]:r["primary_scenario"] for r in manifest_data["fixtures"]}
    sizes = manifest_data["scenario_counts"]
    for result_path in result_files:
        report = json.loads(result_path.read_text(encoding="utf-8"))
        data = defaultdict(list)
        skipped = defaultdict(list)
        for row in report["per_document"]:
            data[labels[row["file"]]].append(row)
        for row in report["skipped"]:
            skipped[labels[row["file"]]].append(row)
        mode = report.get("mode")
        print(f"\n{mode} | {result_path} | {report['scored_count']}/{report['total_fixtures']} scored")
        print("category                scored/total   field accuracy*  median latency ms")
        for category, total in sizes.items():
            rows = data[category]
            score = (f"{mean(r['field_accuracy'] for r in rows if r['field_accuracy'] is not None):.3f}"
                     if rows and all(r['field_accuracy'] is not None for r in rows) else "n/a")
            latency = (f"{median(r['latency_ms'] for r in rows):.2f}" if rows else "n/a")
            print(f"{category:23} {len(rows):>2}/{total:<2}           {score:>6}               {latency:>8}")
            if skipped[category]:
                reasons=sorted(set(row['reason'] for row in skipped[category]))
                print(f"  skipped: {len(skipped[category])} | {', '.join(reasons)}")
        print("*Category score is an unweighted average of per-document field accuracy;"
              " overall weighted field accuracy remains in the original evaluation JSON.")
        print("Only processed documents contribute to accuracy. Review skipped and failures before comparison.")


def main() -> None:
    p = argparse.ArgumentParser(description="Coverage-aware evaluation summaries for fictional invoices")
    p.add_argument("result_files", type=Path, nargs="+", help="Paths to existing evaluation JSON reports")
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args=p.parse_args()
    summarize(args.manifest,args.result_files)


if __name__=="__main__":
    main()
