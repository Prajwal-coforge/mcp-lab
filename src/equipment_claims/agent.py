"""ReAct agent for equipment requests.

Each cycle is Thought, then Action, then Observation. The next action is chosen
from the previous observation. A draft is written only after the tools return,
and a reflection pass checks that draft against those observations before the
decision is final.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from equipment_claims.scenarios import SCENARIOS
from equipment_claims.service import (
    ELIGIBLE_NEW,
    ELIGIBLE_REPLACEMENT,
    age_conflict,
    find_items,
    item_query,
)

_EMPLOYEE_ID = re.compile(r"\b(E\d{4})\b")
_ACCOMMODATION = re.compile(
    r"doctor'?s note|\bphysician\b|\bdisability\b|\bADA\b|medical accommodation",
    re.IGNORECASE,
)
_ADDITIONAL = re.compile(r"\b(second|another|additional|extra)\b", re.IGNORECASE)
_REPLACEMENT = re.compile(
    r"\b(replace|replacement|refresh|broken|worn|too slow|years old)\b",
    re.IGNORECASE,
)


@dataclass
class CaseResult:
    request: str
    trace_lines: list[str] = field(default_factory=list)
    initial_draft: str = ""
    reflection_issues: list[str] = field(default_factory=list)
    revised_draft: str = ""
    final_response: str = ""
    decision: str = ""
    reason: str = ""
    ticket: dict[str, Any] | None = None
    final_reflection_issues: list[str] = field(default_factory=list)

    @property
    def trace(self) -> str:
        return "\n".join(self.trace_lines).rstrip() + "\n"

    @property
    def reflection_confirmed_initial(self) -> bool:
        return not self.reflection_issues

    @property
    def final_reflection_confirmed(self) -> bool:
        return not self.final_reflection_issues


def server_parameters(review_log: Path) -> StdioServerParameters:
    """Launch the MCP server as a stdio subprocess."""
    project = Path(__file__).resolve().parents[2]
    src = project / "src"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "equipment_claims.server"],
        env={
            "PYTHONPATH": str(src),
            "EQUIPMENT_REVIEW_LOG": str(review_log),
            "EQUIPMENT_AS_OF": "2026-09-30",
        },
        cwd=str(project),
    )


def accommodation_note(text: str) -> str | None:
    if _ACCOMMODATION.search(text):
        return (
            "The request cites a medical accommodation or doctor's note, which "
            "standard equipment limits do not decide."
        )
    return None


def request_intent(text: str) -> str:
    additional = _ADDITIONAL.search(text) is not None
    replacement = _REPLACEMENT.search(text) is not None
    if additional and replacement:
        return "unspecified"
    if additional:
        return "additional"
    if replacement:
        return "replacement"
    return "unspecified"


def decide(
    request_text: str,
    employee: dict[str, Any] | None,
    eligibility: dict[str, Any] | None,
) -> tuple[str, str]:
    """Choose approve, deny, or escalate from tool results. Never guess."""
    items = find_items(request_text)
    accommodation = accommodation_note(request_text)
    explanation = "" if eligibility is None else eligibility["explanation"]

    if employee is None or not employee.get("found"):
        return "escalate", explanation or "No directory record for that employee id."

    if len(items) != 1:
        reason = explanation or "The request does not name exactly one catalog item."
        if accommodation:
            reason = f"{reason} {accommodation}"
        return "escalate", reason

    conflict = age_conflict(request_text, employee, items[0])
    blocks = [note for note in (accommodation, conflict) if note]
    if blocks:
        prefix = ""
        if eligibility is not None:
            prefix = (
                f"check_request_eligibility returned {eligibility['status']}: "
                f"{explanation} "
            )
        return "escalate", prefix + " ".join(blocks)

    if eligibility is None:
        return "escalate", "Eligibility was not checked, so there is no decision to make."

    status = eligibility["status"]
    if status == ELIGIBLE_NEW:
        return "approve", explanation
    if status == ELIGIBLE_REPLACEMENT:
        intent = request_intent(request_text)
        if intent == "replacement":
            return "approve", explanation
        if intent == "additional":
            return (
                "deny",
                explanation + " The request asks for an additional unit, which would exceed the count limit.",
            )
        return (
            "escalate",
            explanation
            + " The request does not say whether this is a replacement of the unit on file or an additional unit.",
        )
    if status.startswith("ineligible"):
        return "deny", explanation
    return "escalate", explanation


def initial_draft(
    request_text: str,
    eligibility: dict[str, Any] | None,
) -> str:
    """First pass, before reflection.

    When the item phrase is ambiguous this pass commits to the first catalog
    word it saw. Reflection exists to reject that guess. A single clear
    eligibility status is quoted as-is, including a denial that may still
    ignore an accommodation or a conflicting age claim.
    """
    items = find_items(request_text)
    if len(items) != 1:
        guessed = items[0] if items else "equipment"
        return f"Approved. Issuing a {guessed} because the request mentioned it and asked me to pick."

    status = "" if eligibility is None else eligibility["status"]
    explanation = "" if eligibility is None else eligibility["explanation"]
    if status in {ELIGIBLE_NEW, ELIGIBLE_REPLACEMENT}:
        return f"Approved. {explanation}"
    if status.startswith("ineligible"):
        return f"Denied. {explanation}"
    return f"Escalated. {explanation}"


def _opening(draft: str) -> str | None:
    if draft.startswith("Approved"):
        return "approve"
    if draft.startswith("Denied"):
        return "deny"
    if draft.startswith("Escalated"):
        return "escalate"
    return None


def reflect_on_draft(
    draft: str,
    decision: str,
    reason: str,
    eligibility: dict[str, Any] | None,
) -> list[str]:
    """Check the draft against the decision the observations actually support."""
    issues: list[str] = []
    stated = _opening(draft)
    if stated != decision:
        issues.append(
            "Initial draft opens with "
            f"{stated or 'an unclear decision'}, but the tool observations support "
            f"{decision}. {reason}"
        )
    if stated == "approve" and eligibility is not None and not str(eligibility["status"]).startswith("eligible"):
        issues.append(
            "Draft approves the request, but check_request_eligibility returned "
            f"{eligibility['status']}, not an eligible status."
        )
    if stated == "deny" and decision != "deny":
        issues.append(
            "Draft denies the request from the eligibility result alone. A denial "
            "would ignore a conflicting age claim or an accommodation that the policy tool does not settle."
        )
    if eligibility is not None:
        evidence = eligibility.get("evidence") or {}
        max_count = evidence.get("max_count")
        refresh_years = evidence.get("refresh_years")
        up_to = re.search(r"up to (\d+)", draft)
        if up_to and max_count is not None and int(up_to.group(1)) != max_count:
            issues.append(
                f"Draft says up to {up_to.group(1)}, but get_policy_limits max_count is {max_count}."
            )
        every = re.search(r"every (\d+) years", draft)
        if every and refresh_years is not None and int(every.group(1)) != refresh_years:
            issues.append(
                f"Draft says every {every.group(1)} years, but the policy refresh is {refresh_years} years."
            )
    return issues


def render_final(decision: str, reason: str, ticket: dict[str, Any] | None) -> str:
    if decision == "approve":
        return f"Approved. {reason}"
    if decision == "deny":
        return f"Denied. {reason}"
    recorded = reason if ticket is None else ticket["reason"]
    ticket_id = "pending" if ticket is None else ticket["ticket_id"]
    return (
        f"Escalated. Filed {ticket_id} for human review. "
        f"Reason recorded by flag_for_human_review: {recorded}"
    )


def confirm_final(
    draft: str,
    decision: str,
    eligibility: dict[str, Any] | None,
    ticket: dict[str, Any] | None,
) -> list[str]:
    issues: list[str] = []
    stated = _opening(draft)
    if stated != decision:
        issues.append(f"Final draft says {stated}, but the decision is {decision}.")
    if decision == "escalate":
        if ticket is None:
            issues.append("Escalation draft has no ticket from flag_for_human_review.")
        else:
            if ticket["ticket_id"] not in draft:
                issues.append("Final draft omits the review ticket id.")
            if ticket["reason"] not in draft:
                issues.append("Final draft does not quote the reason stored by flag_for_human_review.")
        if draft.startswith("Approved") or draft.startswith("Denied"):
            issues.append("Final draft still commits to an approval or a denial.")
    else:
        if ticket is not None:
            issues.append("A clear approve or deny must not file a review ticket.")
        explanation = None if eligibility is None else eligibility.get("explanation")
        if explanation and explanation not in draft:
            issues.append("Final draft drops the explanation returned by check_request_eligibility.")
    return issues


def _record(lines: list[str], thought: str, action: str, observation: dict[str, Any]) -> None:
    lines.append(f"Thought: {thought}")
    lines.append(f"Action: {action}")
    lines.append("Observation:")
    lines.append(json.dumps(observation, indent=2))
    lines.append("")


async def _call(client: Client, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    if result.is_error:
        text = " ".join(getattr(block, "text", "") for block in result.content)
        raise RuntimeError(f"{name} failed: {text}")
    data = result.structured_content
    if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], dict):
        return data["result"]
    if not isinstance(data, dict):
        raise RuntimeError(f"{name} returned non-object content: {data!r}")
    return data


async def investigate(client: Client, request_text: str) -> CaseResult:
    case = CaseResult(request=request_text)
    lines = case.trace_lines
    employee_id = None
    id_match = _EMPLOYEE_ID.search(request_text)
    if id_match:
        employee_id = id_match.group(1)

    if employee_id is None:
        thought = "The request has no employee id, so I cannot look anyone up or apply a role policy."
        lines.append(f"Thought: {thought}")
        lines.append("")
        employee = None
        policy = None
        eligibility = None
    else:
        _record(
            lines,
            "I need the directory record before I trust any role or equipment claim in the request.",
            f'get_employee_info(employee_id="{employee_id}")',
            employee := await _call(client, "get_employee_info", {"employee_id": employee_id}),
        )
        if employee["found"]:
            role = employee["role"]
            _record(
                lines,
                f"The directory lists {employee['name']} as {role}. Policy depends on that role, so I need its limits next.",
                f'get_policy_limits(role="{role}")',
                policy := await _call(client, "get_policy_limits", {"role": role}),
            )
        else:
            policy = None
            lines.append(
                "Thought: That employee id is not in the directory. I will not invent a role or apply someone else's policy."
            )
            lines.append("")

        query = item_query(request_text)
        named = find_items(request_text)
        if policy is not None and policy.get("found"):
            policy_note = f"Policy on file: {policy['summary']} "
        else:
            policy_note = ""
        if len(named) == 1:
            item_thought = (
                f"{policy_note}The request names one catalog item, {query}. "
                "I will check that item and only that item."
            )
        else:
            item_thought = (
                f"{policy_note}The request does not name one catalog item (item phrase {query!r}). "
                "I will ask the eligibility tool to classify that phrase, and I will not pick a winner."
            )
        _record(
            lines,
            item_thought,
            f'check_request_eligibility(employee_id="{employee_id}", item="{query}")',
            eligibility := await _call(
                client,
                "check_request_eligibility",
                {"employee_id": employee_id, "item": query},
            ),
        )

    decision, reason = decide(request_text, employee, eligibility)
    draft = initial_draft(request_text, eligibility)
    issues = reflect_on_draft(draft, decision, reason, eligibility)
    case.initial_draft = draft
    case.reflection_issues = issues
    case.decision = decision
    case.reason = reason

    lines.append("Thought: I have a first draft. Before sending it, I will check it against the observations.")
    lines.append("Initial draft:")
    lines.append(draft)
    lines.append("")
    if issues:
        lines.append("Reflection: REVISED")
        for issue in issues:
            lines.append(f"- {issue}")
        lines.append("")
    else:
        lines.append("Reflection: CONFIRMED")
        lines.append(
            "The draft's decision matches the eligibility observation, and every limit it quotes matches the policy tool."
        )
        lines.append("")

    ticket = None
    if decision == "escalate":
        flag_id = employee_id or "UNKNOWN"
        _record(
            lines,
            "The reflected decision is to escalate. I will record that with flag_for_human_review "
            "and I will not approve or deny it myself.",
            "flag_for_human_review(employee_id="
            f'"{flag_id}", request=<original request>, reason=<reflection reason>)',
            ticket := await _call(
                client,
                "flag_for_human_review",
                {"employee_id": flag_id, "request": request_text, "reason": reason},
            ),
        )

    if issues or decision == "escalate":
        final = render_final(decision, reason, ticket)
    else:
        final = draft

    final_issues = confirm_final(final, decision, eligibility, ticket)
    if final_issues:
        final = render_final(decision, reason, ticket)
        final_issues = confirm_final(final, decision, eligibility, ticket)

    case.ticket = ticket
    case.revised_draft = final
    case.final_response = final
    case.final_reflection_issues = final_issues

    if issues or decision == "escalate":
        lines.append("Revised draft:")
        lines.append(final)
        lines.append("")
        if final_issues:
            lines.append("Reflection on revised draft: STILL INCONSISTENT")
            for issue in final_issues:
                lines.append(f"- {issue}")
        else:
            lines.append("Reflection on revised draft: CONFIRMED")
            lines.append(
                "The revised draft matches the decision, quotes only tool-backed facts, "
                "and does not approve or deny an escalated case."
            )
        lines.append("")

    lines.append(f"FINAL DECISION: {decision.upper()}")
    lines.append("Final response:")
    lines.append(final)
    return case


async def run_scenarios(
    review_log: Path,
    scenarios: list[dict[str, str]] | None = None,
) -> tuple[list[str], list[CaseResult]]:
    chosen = scenarios if scenarios is not None else SCENARIOS
    async with Client(server_parameters(review_log)) as client:
        listed = await client.list_tools()
        names = [tool.name for tool in listed.tools]
        results = []
        for scenario in chosen:
            results.append(await investigate(client, scenario["request"]))
        return names, results


def format_session(tool_names: list[str], scenarios: list[dict[str, str]], results: list[CaseResult]) -> str:
    parts = [
        "Connected to MCP server 'Equipment Claims' over stdio.",
        "Tools registered: " + ", ".join(tool_names),
        "",
    ]
    for index, (scenario, result) in enumerate(zip(scenarios, results), start=1):
        parts.append("=" * 72)
        parts.append(f"REQUEST {index} of {len(scenarios)}  [{scenario['id']}]")
        parts.append("=" * 72)
        parts.append("Request:")
        parts.append(scenario["request"])
        parts.append("")
        parts.append(result.trace.rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"
