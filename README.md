# Switchboard

[![CI](https://github.com/tanmaya121099-hub/switchboard/actions/workflows/ci.yml/badge.svg)](https://github.com/tanmaya121099-hub/switchboard/actions/workflows/ci.yml)

**Latency-aware TTS routing for production voice agents.** A benchmark harness that measures what TTS providers actually deliver (not what their marketing says), plus a phone-reachable e-commerce voice agent that uses that data to pick — and fail over between — providers on every call.

Named after the telephone switchboard: the operator that connects each call to the right line. Here, the "operator" is a router that connects each call to the fastest healthy TTS provider, based on measured data.

## Why this exists

Teams deploying voice AI hit the same three walls:

1. **Provider latency is opaque.** What's Sonic-3's p95 TTFB from Bengaluru, under 10 concurrent requests, on Hinglish text? Nobody publishes this.
2. **No failover story.** When the TTS upstream degrades, most agents just get slow or die.
3. **No per-call observability.** "The bot felt laggy yesterday" — which stage ate the budget?

Switchboard answers all three with working code and measured numbers.

## Architecture

```
                      ┌──────────────────────────────────────────┐
  bench (offline)     │  agent (per call)                        │
  ┌────────────────┐  │  Twilio ⇄ /ws (FastAPI)                  │
  │ 4 TTS providers│  │     │                                    │
  │ streamed, timed│  │  pipecat pipeline                        │
  │ TTFB/RTF/gaps  │  │  STT (Deepgram, Hinglish) → LLM (Claude) │
  └───────┬────────┘  │     → TTS ◄── LatencyRouter              │
          │           │              (ranks by bench p95,        │
   results/bench_*.json ─────────────► circuit-breaks failures)  │
          │           │     │                                    │
   results/report.md  │  results/calls/traces.jsonl (per-call)   │
   (publishable)      └──────────────────────────────────────────┘
```

- **Benchmark** (`src/switchboard/bench/`): raw-httpx streaming clients for Cartesia Sonic-3, ElevenLabs Flash, OpenAI TTS, Deepgram Aura. Measures TTFB p50/p95, real-time factor, inter-chunk stalls, error rates, concurrency degradation, and Hinglish handling. Emits JSON + a publishable markdown report.
- **Agent** (`src/switchboard/agent/`): pipecat pipeline behind Twilio media streams, with barge-in, Hinglish STT, playbook-driven conversations, call-setup TTS failover, and JSONL call traces.
- **Playbooks** (`playbooks/`): the use case as YAML. Ships with two e-commerce flows — `cod_confirmation` (flagship demo) and `refund_status` — to prove "same platform, new customer flow in one file".

## Quickstart

### 1. Benchmark (needs only API keys, ~20 min, ~$1–3 in credits)

```bash
cd switchboard
uv venv && uv pip install -e .
cp .env.example .env        # fill in the provider keys you have
uv run switchboard-bench    # prints a cost estimate before running
uv run switchboard-report   # table + results/report.md
```

### 2. Voice agent (needs Twilio number + ngrok)

```bash
uv pip install -e ".[agent]"
ngrok http 8080                          # note the https domain
# .env: set PUBLIC_HOST=<ngrok domain>, TWILIO_*, ANTHROPIC_API_KEY
uv run switchboard-agent
# Twilio console → your number → Voice webhook → https://<PUBLIC_HOST>/voice
```

Call your Twilio number. Asha greets you in Hinglish; try answering in Hindi, English, or mixed — and try interrupting her mid-sentence (barge-in). Switch use case with `PLAYBOOK=refund_status`.

After the call: `results/calls/traces.jsonl` has the full trace — provider picked, router state, events, duration.

## Demo script (60 seconds)

1. Call the number → COD confirmation flow in Hinglish
2. Interrupt mid-sentence → agent stops and yields (barge-in)
3. Show `results/report.md` → "this is why the router picked Cartesia"
4. Kill the Cartesia key, call again → router fails over to the runner-up; show the trace
5. `PLAYBOOK=refund_status`, restart, call again → new use case, zero code changed

## What's deliberately on the roadmap (not faked)

- **Mid-call TTS failover** — swapping the TTS service inside a live pipecat pipeline; today failover happens at call setup + circuit breaker across calls
- **Sarvam AI** as a fifth provider for deeper Indic (Tamil/Telugu/Marathi) support
- **Continuous background probing** so rankings refresh without a manual bench run
- **Real OMS/payments integration** replacing the playbook demo fixtures (tool-calling)
- Outbound dialing, k8s deploy manifests, load tests on the agent path

## Costs

Benchmark run: ~$1–3 (printed before each run). Agent: roughly $0.05–0.15/min across STT + LLM + TTS + Twilio. Twilio trial credit covers the demo.
