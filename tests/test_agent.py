"""The live agent calls local Qwen through Ollama.

The four-request run is skipped when Ollama is not running, which is the case
in CI. Parsing the model's decision line does not call the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import pytest

from equipment_claims.agent import CaseResult, ollama_reachable, parse_decision, run_scenarios, score_run
from equipment_claims.logging_config import configure_logging


def test_configure_logging_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("EQUIPMENT_LOG_FILE", str(tmp_path / "equipment_claims.log"))
    monkeypatch.setenv("EQUIPMENT_LOG_LEVEL", "INFO")
    configure_logging(force=True)
    configure_logging()
    parent = logging.getLogger("equipment_claims")
    assert len(parent.handlers) == 2
    logging.getLogger("equipment_claims.eval").info("scenario=demo expected=approve predicted=approve correct=True")
    text = (tmp_path / "equipment_claims.log").read_text()
    assert "equipment_claims.eval" in text
    assert "correct=True" in text


def test_score_run_counts_matches_and_misses():
    scenarios = [
        {"id": "approve-second-monitor", "expected": "approve"},
        {"id": "deny-extra-monitor", "expected": "deny"},
        {"id": "escalate-ambiguous-item", "expected": "escalate"},
    ]
    results = [
        CaseResult(request="a", decision="approve", tool_calls=["get_employee_info"]),
        CaseResult(request="b", decision="approve", tool_calls=["get_employee_info", "check_request_eligibility"]),
        CaseResult(
            request="c",
            decision="escalate",
            tool_calls=["flag_for_human_review"],
            ticket={"ticket_id": "REV-0001"},
            trace_lines=["Reflection: CONFIRMED"],
        ),
    ]

    score = score_run(scenarios, results)

    assert score["cases"] == 3
    assert score["correct"] == 2
    assert score["accuracy"] == 2 / 3
    assert score["rows"][1]["correct"] is False
    assert score["rows"][2]["ticket_id"] == "REV-0001"
    assert score["by_label"]["deny"] == {"expected": 1, "predicted": 0, "correct": 0}
    assert score["by_label"]["escalate"]["correct"] == 1


def test_parse_decision_reads_the_model_line():
    text = "Reflection: CONFIRMED\nFinal response: Approved.\nFINAL DECISION: APPROVE"
    assert parse_decision(text) == "approve"


@pytest.mark.skipif(
    not ollama_reachable() or os.environ.get("EQUIPMENT_RUN_LLM_TESTS") != "1",
    reason="Set EQUIPMENT_RUN_LLM_TESTS=1 while Ollama is running to call local Qwen.",
)
def test_agent_over_mcp_runs_all_four_requests(tmp_path):
    review_log = tmp_path / "review_queue.json"
    tool_names, results, model, temperature = asyncio.run(run_scenarios(review_log))

    assert model == "qwen3:8b"
    assert temperature > 0
    assert tool_names == [
        "get_employee_info",
        "get_policy_limits",
        "check_request_eligibility",
        "flag_for_human_review",
    ]
    assert [result.decision for result in results] == ["approve", "deny", "escalate", "escalate"]
    for result in results:
        assert "Thought:" in result.trace
        assert "Action:" in result.trace
        assert "Observation:" in result.trace
        assert "Reflection:" in result.trace
    escalated = [result for result in results if result.decision == "escalate"]
    assert all(result.ticket is not None for result in escalated)
    saved = json.loads(review_log.read_text())
    assert len(saved) == 2
