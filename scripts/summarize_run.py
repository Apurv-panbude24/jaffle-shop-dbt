"""Turn dbt's target/run_results.json into a Markdown summary.

Used by the scheduled workflow to populate the GitHub job summary, and handy
locally: `uv run python scripts/summarize_run.py`.
Exits non-zero if any node errored or failed, so it can also gate a job.
"""

import json
import sys
from collections import Counter
from pathlib import Path

RESULTS = Path("target/run_results.json")


def main() -> int:
    if not RESULTS.exists():
        print("No target/run_results.json found. Run `dbt build` first.")
        return 2

    data = json.loads(RESULTS.read_text())
    results = data["results"]
    by_status = Counter(r["status"] for r in results)

    lines = [
        "## dbt run summary",
        "",
        f"- dbt version: `{data['metadata']['dbt_version']}`",
        f"- Elapsed: {data['elapsed_time']:.2f}s",
        "- Nodes: " + ", ".join(f"{n} {s}" for s, n in sorted(by_status.items())),
        "",
        "| Resource type | Count |",
        "|---|---|",
    ]
    by_type = Counter(r["unique_id"].split(".")[0] for r in results)
    lines += [f"| {t} | {n} |" for t, n in sorted(by_type.items())]

    bad = [r for r in results if r["status"] in ("error", "fail")]
    if bad:
        lines += ["", "### Failures", ""]
        lines += [f"- `{r['unique_id']}`: {r['status']} - {r.get('message')}" for r in bad]

    slowest = sorted(results, key=lambda r: r["execution_time"], reverse=True)[:5]
    lines += ["", "### Slowest nodes", "", "| Node | Seconds |", "|---|---|"]
    lines += [f"| `{r['unique_id']}` | {r['execution_time']:.3f} |" for r in slowest]

    print("\n".join(lines))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
