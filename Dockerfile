FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY playbooks ./playbooks

RUN pip install --no-cache-dir ".[agent]"

# Run as non-root; results/ is the only path the agent writes (call traces).
RUN useradd --create-home app && mkdir -p /app/results && chown -R app /app/results
USER app

EXPOSE 8080
CMD ["uvicorn", "switchboard.agent.server:app", "--host", "0.0.0.0", "--port", "8080"]
