# CLAUDE.md — switchboard

Latency-aware TTS routing: benchmark harness + Twilio voice agent (e-commerce playbooks).

## Commands

- `uv run switchboard-bench` — run TTS latency benchmark (needs provider keys in .env)
- `uv run switchboard-report` — aggregate latest results → results/report.md
- `uv run switchboard-agent` — start FastAPI server on :8080 (needs `.[agent]` extra)

## Context for future sessions

- Bench half is dependency-light (httpx only) by design — it must run even if pipecat install fails.
- Agent half is pinned to pipecat-ai 0.0.6x; imports have version-drift fallbacks. If pipecat APIs break, diff against the official twilio-chatbot example.
- Provider pricing in `bench/providers.py` is list pricing as of 2026-07 — verify before the user publishes numbers.
- Failover is at call setup + circuit breaker across calls. Mid-call TTS swap is roadmap — do not fake it.
- Public-demo guards live in server.py/pipeline.py: Twilio signature validation, WS token, MAX_CALLS_PER_DAY, MAX_CALL_SECONDS. User plans a public live demo — keep these intact.
- Playbook YAMLs contain demo fixtures in the system prompt; real integration point is tool-calling (roadmap).

## Known gotcha (this machine)

uv installs can leave site-packages files with the macOS `hidden` flag (inherited from uv's cache via APFS clones). Python ≥3.11 **silently skips hidden `.pth` files**, which breaks the editable install with `ModuleNotFoundError: switchboard`. Fix: `chflags -R nohidden .venv` after any `uv pip install`.

## Conventions

- Secrets only via .env (gitignored). `results/` is gitignored — bench data and call traces land there.
- New use case = new YAML in `playbooks/`, no pipeline changes.
