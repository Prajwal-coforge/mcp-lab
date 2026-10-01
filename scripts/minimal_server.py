"""One-tool server used to prove the MCP client connection before the full server.

This process speaks MCP on stdout. Do not print anything else here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mcp.server import MCPServer

from equipment_claims.service import get_employee_info as get_employee_info_fn

mcp = MCPServer("Equipment Claims Minimal")


@mcp.tool()
def get_employee_info(employee_id: str) -> dict[str, Any]:
    """Return role, tenure, and current equipment on file for an employee id."""
    return get_employee_info_fn(employee_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
