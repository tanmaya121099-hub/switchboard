"""Latency-aware TTS provider routing with a circuit breaker.

The router closes the loop between the benchmark and the agent: providers are
ranked by measured TTFB p95 from the most recent bench run, and a provider
that fails repeatedly is taken out of rotation for a cooldown window so the
next call lands on the runner-up instead of a dead upstream.

Scope note: selection and failover happen at call setup. Swapping the TTS
service mid-call is a documented roadmap item, not something this fakes.
"""

import json
import os
from time import monotonic

from loguru import logger

from switchboard.config import RESULTS_DIR
from switchboard.bench.metrics import percentile
from switchboard.bench.providers import ENV_KEYS

# Fallback ranking when no bench data exists yet (rough public-latency order).
DEFAULT_ORDER = ["cartesia", "deepgram", "elevenlabs", "openai"]

MAX_CONSECUTIVE_FAILURES = 3
COOLDOWN_SECONDS = 60.0


class LatencyRouter:
    def __init__(self, results_dir=RESULTS_DIR):
        self._results_dir = results_dir
        self._ttfb_p95 = self._load_rankings()
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def _load_rankings(self) -> dict[str, float]:
        files = sorted(self._results_dir.glob("bench_*.json"))
        if not files:
            logger.warning("no bench results found — using DEFAULT_ORDER; run switchboard-bench")
            return {}
        data = json.loads(files[-1].read_text())
        rankings: dict[str, float] = {}
        for provider in data["meta"]["providers"]:
            ttfbs = [
                r["ttfb_ms"]
                for r in data["results"]
                if r["provider"] == provider and r["ok"] and r["concurrency"] == 1
            ]
            p95 = percentile(ttfbs, 95)
            if p95 is not None:
                rankings[provider] = p95
        logger.info(f"router rankings from {files[-1].name}: {rankings}")
        return rankings

    def _configured(self, name: str) -> bool:
        return bool(os.getenv(ENV_KEYS[name]))

    def _circuit_open(self, name: str) -> bool:
        return monotonic() < self._open_until.get(name, 0.0)

    def ranked(self) -> list[str]:
        """Healthy, configured providers, fastest measured first."""
        measured = sorted(self._ttfb_p95, key=self._ttfb_p95.get)
        order = measured + [p for p in DEFAULT_ORDER if p not in measured]
        return [p for p in order if self._configured(p) and not self._circuit_open(p)]

    def pick(self) -> str:
        candidates = self.ranked()
        if not candidates:
            raise RuntimeError("no TTS provider available: check API keys / all circuits open")
        return candidates[0]

    def record_success(self, name: str) -> None:
        self._failures[name] = 0

    def record_failure(self, name: str) -> None:
        self._failures[name] = self._failures.get(name, 0) + 1
        if self._failures[name] >= MAX_CONSECUTIVE_FAILURES:
            self._open_until[name] = monotonic() + COOLDOWN_SECONDS
            self._failures[name] = 0
            logger.warning(f"circuit OPEN for {name} ({COOLDOWN_SECONDS}s cooldown)")

    def snapshot(self) -> dict:
        return {
            "rankings_ttfb_p95_ms": self._ttfb_p95,
            "ranked_now": self.ranked(),
            "circuits_open": [p for p in ENV_KEYS if self._circuit_open(p)],
        }
