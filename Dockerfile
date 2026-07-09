FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY playbooks ./playbooks

RUN pip install --no-cache-dir ".[agent]"

EXPOSE 8080
CMD ["uvicorn", "switchboard.agent.server:app", "--host", "0.0.0.0", "--port", "8080"]
