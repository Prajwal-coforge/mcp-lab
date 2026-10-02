"""ReAct agent for equipment requests.

Local Qwen, through Ollama, chooses every tool and the final decision. Each
turn is a model call with temperature above 0, so the same request can take a
different path on another run. This module does not decide approve, deny, or
escalate in Python.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from equipment_claims.logging_config import configure_logging
from equipment_claims.scenarios import SCENARIOS

_DECISION = re.compile(r"FINAL DECISION:\s*(APPROVE|DENY|ESCALATE)", re.IGNORECASE)
_REFLECTION = re.compile(r"Reflection:\s*(CONFIRMED|REVISED)", re.IGNORECASE)
logger = logging.getLogger("equipment_claims.eval")
agent_logger = logging.getLogger("equipment_claims.agent")

SYSTEM_PROMPT = """\
You handle internal IT equipment requests. You have tools. You decide which
tool to call, whether you need another one, and whether the request is
approved, denied, or escalated. Every fact you state must come from a tool result.

Call get_employee_info first. When you call get_policy_limits, pass the role
string that tool returned, exactly. Call check_request_eligibility before you
approve or deny. If you escalate, you must call flag_for_human_review yourself
and include its ticket id.

Approve only when one catalog item is clearly within policy. Deny only when
that one item is clearly outside policy and the employee does not dispute the
record or cite a doctor's note. If the request names more than one kind of
equipment, or tells you to pick one, do not choose. That is ambiguous: call
flag_for_human_review and escalate. Also escalate when the employee is missing,
the record conflicts with what they say, or a doctor's note or other
accommodation is cited.

When you are ready to draft, do not call a tool. Write:

Draft: ...
FINAL DECISION: APPROVE or DENY or ESCALATE

You will then be asked to reflect. If the draft states something the tools did
not return, call the missing tool or revise the draft. If it matches, reply:

Reflection: CONFIRMED
Final response: ...
FINAL DECISION: APPROVE or DENY or ESCALATE

If you change it, reply:

Reflection: REVISED
Issues: ...
Final response: ...
FINAL DECISION: APPROVE or DENY or ESCALATE
"""

REFLECTION_PROMPT = """\
Reflect on the draft you just wrote before it is sent.
Does every fact in it come from a tool observation in this conversation?
Does it approve or deny something those observations did not settle?
If the employee named more than one item, or told you to pick, approving one
of them is a guess. Call flag_for_human_review and change the decision to ESCALATE.
If you still need a tool, call that tool now.
If the draft is supported, confirm it.
If it overclaims, revise it.
Use the reflection format from your instructions.
"""


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
    tool_calls: list[str] = field(default_factory=list)

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


def llm_settings() -> tuple[str, str, float]:
    """Return the Ollama host, model id, and temperature. Temperature stays above 0."""
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("EQUIPMENT_LLM_MODEL", "qwen3:8b")
    temperature = float(os.environ.get("EQUIPMENT_LLM_TEMPERATURE", "0.8"))
    if temperature <= 0:
        raise RuntimeError("EQUIPMENT_LLM_TEMPERATURE must be greater than 0 so the agent is non-deterministic.")
    return host, model, temperature


def ollama_reachable(host: str | None = None) -> bool:
    base = host or llm_settings()[0]
    try:
        with urllib.request.urlopen(f"{base}/api/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def reflection_outcome(result: CaseResult) -> str:
    if result.reflection_issues:
        return "revised"
    if result.final_reflection_issues:
        return "unlabeled"
    if "Reflection: CONFIRMED" in result.trace:
        return "confirmed"
    return "unlabeled"


def score_run(scenarios: list[dict[str, str]], results: list[CaseResult]) -> dict[str, Any]:
    """Compare each model decision with the labeled scenario. This is the eval score."""
    rows = []
    for scenario, result in zip(scenarios, results):
        predicted = result.decision or "unresolved"
        ticket = result.ticket or {}
        rows.append(
            {
                "id": scenario["id"],
                "expected": scenario["expected"],
                "predicted": predicted,
                "correct": predicted == scenario["expected"],
                "reflection": reflection_outcome(result),
                "tool_calls": len(result.tool_calls),
                "tools": list(result.tool_calls),
                "ticket_id": ticket.get("ticket_id"),
                "escalation_reason": ticket.get("reason"),
            }
        )
    correct = sum(1 for row in rows if row["correct"])
    count = len(rows)
    by_label = {}
    for label in ("approve", "deny", "escalate"):
        by_label[label] = {
            "expected": sum(1 for row in rows if row["expected"] == label),
            "predicted": sum(1 for row in rows if row["predicted"] == label),
            "correct": sum(1 for row in rows if row["expected"] == label and row["correct"]),
        }
    return {
        "cases": count,
        "correct": correct,
        "accuracy": (correct / count) if count else 0.0,
        "by_label": by_label,
        "rows": rows,
    }


def log_score(score: dict[str, Any]) -> None:
    configure_logging()
    for row in score["rows"]:
        logger.info(
            "scenario=%s expected=%s predicted=%s correct=%s reflection=%s tool_calls=%d tools=%s ticket=%s",
            row["id"],
            row["expected"],
            row["predicted"],
            row["correct"],
            row["reflection"],
            row["tool_calls"],
            ",".join(row["tools"]) or "none",
            row["ticket_id"] or "none",
        )
    logger.info(
        "cases=%d correct=%d accuracy=%.2f",
        score["cases"],
        score["correct"],
        score["accuracy"],
    )
    for label, counts in score["by_label"].items():
        logger.info(
            "label=%s expected=%d predicted=%d correct=%d",
            label,
            counts["expected"],
            counts["predicted"],
            counts["correct"],
        )


def parse_decision(text: str) -> str:
    match = _DECISION.search(text)
    if match is None:
        return ""
    return match.group(1).lower()


def _reflection_label(text: str) -> str:
    match = _REFLECTION.search(text)
    if match is None:
        return ""
    return match.group(1).upper()


def _ollama_tools(listed: Any) -> list[dict[str, Any]]:
    tools = []
    for tool in listed.tools:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.input_schema,
                },
            }
        )
    return tools


def _unwrap(result: Any) -> dict[str, Any]:
    if result.is_error:
        text = " ".join(getattr(block, "text", "") for block in result.content)
        return {"is_error": True, "error": text}
    data = result.structured_content
    if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], dict):
        return data["result"]
    if isinstance(data, dict):
        return data
    return {"result": data}


def _chat(host: str, model: str, temperature: float, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": temperature},
    }
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.load(response)
    message = body.get("message")
    if not isinstance(message, dict):
        raise RuntimeError(f"Ollama returned no message: {body}")
    return message


def _arguments(tool_call: dict[str, Any]) -> dict[str, Any]:
    function = tool_call.get("function") or {}
    raw = function.get("arguments", {})
    if isinstance(raw, str):
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    return dict(raw) if isinstance(raw, dict) else {}


async def investigate(
    mcp: Client,
    request_text: str,
    host: str,
    model: str,
    temperature: float,
    tools: list[dict[str, Any]],
) -> CaseResult:
    case = CaseResult(request=request_text)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": request_text},
    ]
    phase = "draft"
    for _step in range(10):
        message = _chat(host, model, temperature, messages, tools)
        thinking = (message.get("thinking") or "").strip()
        content = (message.get("content") or "").strip()
        tool_calls = message.get("tool_calls") or []
        assistant: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
        if message.get("thinking"):
            assistant["thinking"] = message["thinking"]
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        messages.append(assistant)

        if tool_calls:
            thought = thinking or content or "The model selected a tool from the conversation so far."
            case.trace_lines.append(f"Thought: {thought}")
            for tool_call in tool_calls:
                function = tool_call.get("function") or {}
                name = function.get("name", "")
                arguments = _arguments(tool_call)
                case.tool_calls.append(name)
                agent_logger.info("tool=%s arguments=%s", name, arguments)
                observation = _unwrap(await mcp.call_tool(name, arguments))
                case.trace_lines.append(f"Action: {name}({json.dumps(arguments)})")
                case.trace_lines.append("Observation:")
                case.trace_lines.append(json.dumps(observation, indent=2))
                case.trace_lines.append("")
                if name == "flag_for_human_review" and not observation.get("is_error"):
                    case.ticket = observation
                messages.append(
                    {
                        "role": "tool",
                        "tool_name": name,
                        "content": json.dumps(observation),
                    }
                )
            continue

        text = content or thinking
        if phase == "draft":
            case.initial_draft = text
            if thinking:
                case.trace_lines.append(f"Thought: {thinking}")
            case.trace_lines.append("Initial draft:")
            case.trace_lines.append(text)
            case.trace_lines.append("")
            messages.append({"role": "user", "content": REFLECTION_PROMPT})
            phase = "reflect"
            continue

        label = _reflection_label(text)
        case.final_response = text
        case.revised_draft = text
        case.decision = parse_decision(text)
        case.reason = text
        if thinking:
            case.trace_lines.append(f"Thought: {thinking}")
        if label == "REVISED":
            case.reflection_issues = [text]
            case.trace_lines.append("Reflection: REVISED")
        elif label == "CONFIRMED":
            case.trace_lines.append("Reflection: CONFIRMED")
        else:
            case.final_reflection_issues = ["The model did not label the reflection CONFIRMED or REVISED."]
            case.trace_lines.append("Reflection:")
        case.trace_lines.append(text)
        case.trace_lines.append("")
        case.trace_lines.append(f"FINAL DECISION: {case.decision.upper() or 'UNRESOLVED'}")
        case.trace_lines.append("Final response:")
        case.trace_lines.append(text)
        return case

    raise RuntimeError("The model did not finish within 10 turns.")


async def run_scenarios(
    review_log: Path,
    scenarios: list[dict[str, str]] | None = None,
) -> tuple[list[str], list[CaseResult], str, float]:
    configure_logging()
    host, model, temperature = llm_settings()
    agent_logger.info("model=%s temperature=%s host=%s", model, temperature, host)
    if not ollama_reachable(host):
        raise RuntimeError(f"Ollama is not running at {host}. Start it and pull {model}.")
    chosen = scenarios if scenarios is not None else SCENARIOS
    async with Client(server_parameters(review_log)) as mcp:
        listed = await mcp.list_tools()
        names = [tool.name for tool in listed.tools]
        tools = _ollama_tools(listed)
        results = []
        for scenario in chosen:
            results.append(await investigate(mcp, scenario["request"], host, model, temperature, tools))
        log_score(score_run(chosen, results))
        return names, results, model, temperature


def format_session(
    tool_names: list[str],
    scenarios: list[dict[str, str]],
    results: list[CaseResult],
    model: str,
    temperature: float,
) -> str:
    parts = [
        "Connected to MCP server 'Equipment Claims' over stdio.",
        f"Model: {model} via Ollama",
        f"Temperature: {temperature}. The model chooses each tool and the decision.",
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
