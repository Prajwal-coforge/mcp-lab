"""The eval harness grades a finished run without calling the model."""

from equipment_claims.agent import CaseResult, score_run
from equipment_claims.eval_harness import REQUIRED_TOOLS, build_report, format_report

TOOLS = list(REQUIRED_TOOLS)


def _result(decision: str, tools: list[str], ticket: dict | None = None, reflection: str = "confirmed") -> CaseResult:
    trace = ["Reflection: CONFIRMED"] if reflection == "confirmed" else ["Reflection: REVISED"]
    issues = ["changed the draft"] if reflection == "revised" else []
    return CaseResult(
        request="request",
        decision=decision,
        tool_calls=tools,
        ticket=ticket,
        trace_lines=trace,
        reflection_issues=issues,
    )


def _score(results: list[CaseResult]) -> dict:
    scenarios = [
        {"id": "approve-second-monitor", "expected": "approve"},
        {"id": "deny-extra-monitor", "expected": "deny"},
        {"id": "escalate-ambiguous-item", "expected": "escalate"},
        {"id": "escalate-conflict-and-accommodation", "expected": "escalate"},
    ]
    return score_run(scenarios, results)


def _passing_results() -> list[CaseResult]:
    lookup = ["get_employee_info", "get_policy_limits", "check_request_eligibility"]
    return [
        _result("approve", lookup),
        _result("deny", lookup),
        _result(
            "escalate",
            lookup + ["flag_for_human_review"],
            {"ticket_id": "REV-0001", "reason": "The request names two items."},
            reflection="revised",
        ),
        _result(
            "escalate",
            lookup + ["flag_for_human_review"],
            {"ticket_id": "REV-0002", "reason": "The record conflicts with the stated age, and a doctor's note is attached."},
        ),
    ]


def test_passing_run_meets_part_six():
    report = build_report(_score(_passing_results()), model="qwen3:8b", temperature=0.8, tool_names=TOOLS)

    assert report["passed"] is True
    assert report["failures"] == []
    assert report["cases"] == 4
    assert report["accuracy"] == 1.0
    text = format_report(report)
    assert "passed=true" in text
    assert "REV-0001" in text
    assert "The request names two items." in text


def test_wrong_decision_fails():
    results = _passing_results()
    results[2] = _result("approve", ["get_employee_info", "check_request_eligibility"])
    report = build_report(_score(results), model="qwen3:8b", temperature=0.8, tool_names=TOOLS)

    assert report["passed"] is False
    assert any("expected escalate, predicted approve" in failure for failure in report["failures"])
    assert any("flag_for_human_review" in failure for failure in report["failures"])


def test_escalation_without_a_ticket_fails():
    results = _passing_results()
    results[3] = _result("escalate", ["get_employee_info", "get_policy_limits", "check_request_eligibility"])
    report = build_report(_score(results), model="qwen3:8b", temperature=0.8, tool_names=TOOLS)

    assert report["passed"] is False
    assert any("did not call flag_for_human_review" in failure for failure in report["failures"])


def test_identical_escalation_reasons_fail():
    results = _passing_results()
    results[3] = _result(
        "escalate",
        ["get_employee_info", "flag_for_human_review"],
        {"ticket_id": "REV-0002", "reason": "The request names two items."},
    )
    report = build_report(_score(results), model="qwen3:8b", temperature=0.8, tool_names=TOOLS)

    assert report["passed"] is False
    assert "escalation reasons are not distinct" in report["failures"]


def test_missing_reflection_fails():
    results = _passing_results()
    for result in results:
        result.trace_lines = []
        result.reflection_issues = []
    report = build_report(_score(results), model="qwen3:8b", temperature=0.8, tool_names=TOOLS)

    assert report["passed"] is False
    assert "reflection did not confirm or revise any draft" in report["failures"]
