"""Eval harness for the four labeled equipment requests.

The harness runs the agent, scores each decision against the scenario label,
and fails the run when Part 6 of the lab is not met: one correct approve, one
correct deny, and two correct escalations that each opened a review ticket
for a different reason. At least one draft must be confirmed or revised.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from equipment_claims.agent import run_scenarios, score_run
from equipment_claims.logging_config import configure_logging
from equipment_claims.scenarios import SCENARIOS

logger = logging.getLogger("equipment_claims.eval")

REQUIRED_TOOLS = (
    "get_employee_info",
    "get_policy_limits",
    "check_request_eligibility",
    "flag_for_human_review",
)


def build_report(
    score: dict[str, Any],
    *,
    model: str,
    temperature: float,
    tool_names: list[str],
) -> dict[str, Any]:
    """Turn one scored run into a pass/fail report."""
    failures: list[str] = []
    missing = [name for name in REQUIRED_TOOLS if name not in tool_names]
    if missing:
        failures.append("MCP server is missing tools: " + ", ".join(missing))
    if score["cases"] < 4:
        failures.append(f"eval set has {score['cases']} cases; at least 4 are required")

    for row in score["rows"]:
        if not row["correct"]:
            failures.append(
                f"{row['id']}: expected {row['expected']}, predicted {row['predicted']}"
            )

    by_label = score["by_label"]
    if by_label["approve"]["correct"] < 1:
        failures.append("no clear approve case was decided correctly")
    if by_label["deny"]["correct"] < 1:
        failures.append("no clear deny case was decided correctly")
    if by_label["escalate"]["correct"] < 2:
        failures.append("fewer than two ambiguous cases were escalated correctly")

    reasons: list[str] = []
    for row in score["rows"]:
        if row["expected"] != "escalate":
            continue
        called_flag = "flag_for_human_review" in row["tools"] and bool(row["ticket_id"])
        if not called_flag:
            failures.append(f"{row['id']}: escalation did not call flag_for_human_review")
            continue
        reason = (row.get("escalation_reason") or "").strip()
        if not reason:
            failures.append(f"{row['id']}: escalation has no reason")
        else:
            reasons.append(reason)
    if len(reasons) >= 2 and len(set(reasons)) < len(reasons):
        failures.append("escalation reasons are not distinct")

    if not any(row["reflection"] in {"confirmed", "revised"} for row in score["rows"]):
        failures.append("reflection did not confirm or revise any draft")

    return {
        "model": model,
        "temperature": temperature,
        "tool_names": list(tool_names),
        "passed": not failures,
        "failures": failures,
        "cases": score["cases"],
        "correct": score["correct"],
        "accuracy": score["accuracy"],
        "by_label": score["by_label"],
        "rows": score["rows"],
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        "Eval harness",
        f"Model: {report['model']}  temperature: {report['temperature']}",
        f"Tools: {', '.join(report['tool_names'])}",
        (
            f"cases={report['cases']} correct={report['correct']} "
            f"accuracy={report['accuracy']:.2f} passed={str(report['passed']).lower()}"
        ),
        "",
    ]
    for row in report["rows"]:
        lines.append(
            f"{row['id']} expected={row['expected']} predicted={row['predicted']} "
            f"correct={row['correct']} reflection={row['reflection']} "
            f"tool_calls={row['tool_calls']} ticket={row['ticket_id'] or 'none'}"
        )
        if row.get("escalation_reason"):
            lines.append(f"  reason: {row['escalation_reason']}")
    if report["failures"]:
        lines.append("")
        lines.append("Failures:")
        lines.extend(f"- {failure}" for failure in report["failures"])
    return "\n".join(lines).rstrip() + "\n"


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")


async def execute(review_log: Path, report_path: Path) -> dict[str, Any]:
    """Run the labeled scenarios through the live agent and write the report."""
    configure_logging()
    if review_log.exists():
        review_log.unlink()
    tool_names, results, model, temperature = await run_scenarios(review_log, SCENARIOS)
    report = build_report(
        score_run(SCENARIOS, results),
        model=model,
        temperature=temperature,
        tool_names=tool_names,
    )
    write_report(report_path, report)
    logger.info(
        "eval passed=%s cases=%d correct=%d accuracy=%.2f report=%s",
        str(report["passed"]).lower(),
        report["cases"],
        report["correct"],
        report["accuracy"],
        report_path,
    )
    return report
