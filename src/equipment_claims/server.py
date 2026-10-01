"""Equipment-claims MCP server.

Run over stdio (the client launches this process):

    python -m equipment_claims.server
"""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from equipment_claims.service import (
    check_request_eligibility as check_request_eligibility_fn,
)
from equipment_claims.service import flag_for_human_review as flag_for_human_review_fn
from equipment_claims.service import get_employee_info as get_employee_info_fn
from equipment_claims.service import get_policy_limits as get_policy_limits_fn

mcp = MCPServer(
    "Equipment Claims",
    instructions=(
        "Look up the employee and the role policy before deciding an equipment "
        "request. Approve only a clear within-policy result. Deny only a clear "
        "outside-policy result. If the tools return an ambiguous status, or the "
        "request disputes the record, call flag_for_human_review instead of guessing."
    ),
)


@mcp.tool()
def get_employee_info(employee_id: str) -> dict[str, Any]:
    """Return role, tenure, and current equipment on file for an employee id."""
    return get_employee_info_fn(employee_id)


@mcp.tool()
def get_policy_limits(role: str) -> dict[str, Any]:
    """Return what a role is eligible for, including count limits and refresh cadence."""
    return get_policy_limits_fn(role)


@mcp.tool()
def check_request_eligibility(employee_id: str, item: str) -> dict[str, Any]:
    """Return whether an item is within policy, outside policy, or ambiguous.

    item may be a catalog key such as monitor, or a short phrase. A phrase that
    names more than one catalog item is ambiguous and is not resolved here.
    """
    return check_request_eligibility_fn(employee_id, item)


@mcp.tool()
def flag_for_human_review(employee_id: str, request: str, reason: str) -> dict[str, Any]:
    """Escalate a request to a human reviewer. This does not approve or deny it."""
    return flag_for_human_review_fn(employee_id, request, reason)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
