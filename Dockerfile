FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md REQUIREMENTS.md ./
COPY src ./src
COPY tests ./tests
COPY scripts ./scripts

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV EQUIPMENT_AS_OF=2026-09-30

# Default process runs the unit tests. Start the MCP server with:
#   docker run --rm -i mcp-lab python -m equipment_claims.server
CMD ["pytest", "-v"]
