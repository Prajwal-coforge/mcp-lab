"""Connect to the one-tool server and call get_employee_info."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
SERVER = Path(__file__).resolve().parent / "minimal_server.py"


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER)],
        env={
            "PYTHONPATH": str(ROOT / "src"),
            "EQUIPMENT_AS_OF": "2026-09-30",
        },
        cwd=str(ROOT),
    )
    async with Client(params) as client:
        print(f"Connected to: {client.server_info.name}")
        listed = await client.list_tools()
        print("Tools registered:", [tool.name for tool in listed.tools])
        result = await client.call_tool("get_employee_info", {"employee_id": "E1001"})
        print(f"get_employee_info is_error: {result.is_error}")
        print(json.dumps(result.structured_content, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
