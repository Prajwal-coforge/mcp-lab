"""Run the four demo requests and print the ReAct traces."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from equipment_claims.agent import format_session, run_scenarios
from equipment_claims.scenarios import SCENARIOS


async def main() -> None:
    review_log = ROOT / "var" / "review_queue.json"
    review_log.parent.mkdir(parents=True, exist_ok=True)
    if review_log.exists():
        review_log.unlink()
    tool_names, results = await run_scenarios(review_log, SCENARIOS)
    print(format_session(tool_names, SCENARIOS, results), end="")
    print("=" * 72)
    print("Review queue written to", review_log)
    print(review_log.read_text(), end="")


if __name__ == "__main__":
    asyncio.run(main())
