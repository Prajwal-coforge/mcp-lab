"""Decision rules, reflection, and the agent talking to the MCP server."""

from __future__ import annotations

import asyncio
import json

from equipment_claims.agent import decide, initial_draft, reflect_on_draft, run_scenarios
from equipment_claims.scenarios import SCENARIOS
from equipment_claims.service import check_request_eligibility, get_employee_info


def test_clear_replacement_is_approved():
    employee = get_employee_info("E1001")
    eligibility = check_request_eligibility("E1001", "laptop")
    request = "Employee E1001 needs a laptop replacement. The current one is too slow."

    decision, reason = decide(request, employee, eligibility)

    assert eligibility["status"] == "eligible_replacement"
    assert decision == "approve"
    assert reason == eligibility["explanation"]


def test_extra_unit_at_the_cap_is_denied_even_if_a_replacement_would_be_allowed():
    employee = get_employee_info("E1001")
    eligibility = check_request_eligibility("E1001", "laptop")
    request = "Employee E1001 wants a second laptop."

    decision, _reason = decide(request, employee, eligibility)

    assert decision == "deny"


def test_replacement_window_without_a_stated_intent_is_escalated():
    employee = get_employee_info("E1001")
    eligibility = check_request_eligibility("E1001", "laptop")
    request = "Employee E1001 needs a laptop."

    decision, reason = decide(request, employee, eligibility)

    assert decision == "escalate"
    assert "replacement" in reason
    assert "additional" in reason


def test_unknown_employee_is_escalated():
    employee = get_employee_info("E1006")
    eligibility = check_request_eligibility("E1006", "laptop")

    decision, _reason = decide("Employee E1006 needs a laptop.", employee, eligibility)

    assert employee["found"] is False
    assert eligibility["status"] == "not_found"
    assert decision == "escalate"


def test_reflection_rejects_a_guess_and_a_denial_that_ignores_an_accommodation():
    ambiguous_request = SCENARIOS[2]["request"]
    ambiguous = check_request_eligibility("E1005", "screen or dock")
    employee = get_employee_info("E1005")
    decision, reason = decide(ambiguous_request, employee, ambiguous)
    draft = initial_draft(ambiguous_request, ambiguous)
    issues = reflect_on_draft(draft, decision, reason, ambiguous)

    assert decision == "escalate"
    assert draft.startswith("Approved")
    assert issues

    conflict_request = SCENARIOS[3]["request"]
    conflict_employee = get_employee_info("E1007")
    conflict = check_request_eligibility("E1007", "laptop")
    conflict_decision, conflict_reason = decide(conflict_request, conflict_employee, conflict)
    conflict_draft = initial_draft(conflict_request, conflict)
    conflict_issues = reflect_on_draft(conflict_draft, conflict_decision, conflict_reason, conflict)

    assert conflict["status"] == "ineligible_too_soon"
    assert conflict_draft.startswith("Denied")
    assert conflict_decision == "escalate"
    assert conflict_issues
    assert "doctor" in conflict_reason
    assert "4 years" in conflict_reason


def test_agent_over_mcp_runs_all_four_requests(tmp_path):
    review_log = tmp_path / "review_queue.json"
    tool_names, results = asyncio.run(run_scenarios(review_log))

    assert tool_names == [
        "get_employee_info",
        "get_policy_limits",
        "check_request_eligibility",
        "flag_for_human_review",
    ]
    assert [result.decision for result in results] == ["approve", "deny", "escalate", "escalate"]

    approve, deny, ambiguous, conflict = results
    assert approve.reflection_confirmed_initial
    assert approve.ticket is None
    assert approve.final_response.startswith("Approved")
    assert "up to 2" in approve.final_response
    assert approve.final_reflection_confirmed

    assert deny.reflection_confirmed_initial
    assert deny.ticket is None
    assert deny.final_response.startswith("Denied")
    assert "every 3 years" in deny.final_response

    assert ambiguous.initial_draft.startswith("Approved")
    assert not ambiguous.reflection_confirmed_initial
    assert ambiguous.final_response.startswith("Escalated")
    assert ambiguous.ticket is not None
    assert "monitor" in ambiguous.ticket["reason"]
    assert "docking_station" in ambiguous.ticket["reason"]
    assert ambiguous.ticket["ticket_id"] in ambiguous.final_response
    assert ambiguous.final_reflection_confirmed

    assert conflict.initial_draft.startswith("Denied")
    assert not conflict.reflection_confirmed_initial
    assert conflict.final_response.startswith("Escalated")
    assert "doctor" in conflict.ticket["reason"]
    assert "4 years" in conflict.ticket["reason"]
    assert conflict.ticket["reason"] in conflict.final_response

    for result in results:
        assert "Thought:" in result.trace
        assert "Action:" in result.trace
        assert "Observation:" in result.trace
        assert "Reflection:" in result.trace

    saved = json.loads(review_log.read_text())
    assert [row["ticket_id"] for row in saved] == ["REV-0001", "REV-0002"]
    assert saved[0]["employee_id"] == "E1005"
    assert saved[1]["employee_id"] == "E1007"
