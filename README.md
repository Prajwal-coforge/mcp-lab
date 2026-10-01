# Equipment claims MCP lab

Python 3.10 or newer is required. The system Python on this machine may be 3.9; use the project virtualenv.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

One-tool connection check:

```bash
.venv/bin/python scripts/minimal_client.py
```

Unit tests, the same command CI runs:

```bash
.venv/bin/pytest -v
```

Four-request ReAct demo:

```bash
.venv/bin/python scripts/run_agent_demo.py
```

Policy rules are in `REQUIREMENTS.md`. The server is `src/equipment_claims/server.py`. The agent is `src/equipment_claims/agent.py`.

## Docker

Build the image and run the unit tests inside it:

```bash
docker build -t mcp-lab .
docker run --rm mcp-lab
```

The image's default command is `pytest -v`. Start the MCP server in the same image with:

```bash
docker run --rm -i mcp-lab python -m equipment_claims.server
```
