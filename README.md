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

Four-request ReAct demo. Local Qwen (`qwen3:8b` through Ollama) chooses each tool and the decision. Temperature defaults to 0.8, so the run is non-deterministic:

```bash
ollama serve
.venv/bin/python scripts/run_agent_demo.py
```

Eval harness. Same four labeled requests. It writes `var/eval_report.json` and exits non-zero unless the run has one correct approve, one correct deny, and two escalations that each called `flag_for_human_review` with a different reason, plus a reflection that confirms or revises a draft:

```bash
ollama serve
.venv/bin/python scripts/run_eval.py
```

That live run is local. GitHub Actions has no Ollama, so the `eval-harness` job runs `tests/test_eval.py`, which grades finished results and does not call the model. Docker's default command is still `pytest -v`, which includes those checks and does not call Qwen.

Policy rules are in `REQUIREMENTS.md`. The server is `src/equipment_claims/server.py`. The agent is `src/equipment_claims/agent.py`. Logs go to stderr and `var/equipment_claims.log`. Set `EQUIPMENT_LOG_LEVEL` to change the level.

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
