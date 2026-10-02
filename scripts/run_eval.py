"""Run the eval harness against local Qwen and exit non-zero on a failed report."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from equipment_claims.eval_harness import execute, format_report


async def main() -> int:
    report = await execute(
        ROOT / "var" / "eval_review_queue.json",
        ROOT / "var" / "eval_report.json",
    )
    print(format_report(report), end="")
    print("Report written to", ROOT / "var" / "eval_report.json")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
