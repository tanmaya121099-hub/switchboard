"""Central configuration. All secrets come from .env — never hardcoded."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

RESULTS_DIR = PROJECT_ROOT / "results"
CALLS_DIR = RESULTS_DIR / "calls"
PLAYBOOKS_DIR = PROJECT_ROOT / "playbooks"

# Benchmark requests raw PCM so time-to-first-byte means "first audible audio",
# not "first byte of a container header".
BENCH_SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2  # pcm_s16le

# Twilio media streams are fixed at 8 kHz mulaw; pipecat resamples for the wire.
TELEPHONY_SAMPLE_RATE = 8_000


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name) or default
    if value is None:
        raise RuntimeError(
            f"Missing required env var {name} — copy .env.example to .env and fill it in"
        )
    return value
