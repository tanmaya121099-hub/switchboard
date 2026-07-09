"""Benchmark runner.

Usage:
    uv run switchboard-bench                       # all configured providers
    uv run switchboard-bench --providers cartesia elevenlabs --trials 10
    uv run switchboard-bench --concurrency 1 2 5 10

Sequential trials measure baseline TTFB per text; the concurrency ramp then
measures how TTFB degrades when N requests hit the provider at once — the
number that decides whether a provider survives a call-center-scale rollout.
"""

import argparse
import asyncio
import json
from datetime import datetime, timezone

import httpx
from loguru import logger

from switchboard.config import RESULTS_DIR, env
from switchboard.bench.metrics import StreamStats, measure_stream
from switchboard.bench.providers import (
    ENV_KEYS,
    PRICING_USD_PER_M_CHARS,
    PROVIDERS,
    available_providers,
)
from switchboard.bench.texts import TEXTS

REQUEST_TIMEOUT_S = 30
PAUSE_BETWEEN_TRIALS_S = 0.5  # avoid tripping per-second rate limits


def estimate_cost_usd(provider_names: list[str], trials: int, concurrency: list[int]) -> float:
    seq_chars = sum(len(t) for t in TEXTS.values()) * trials
    conc_chars = len(TEXTS["medium"]) * sum(c for c in concurrency if c > 1)
    per_provider = seq_chars + conc_chars
    return sum(per_provider / 1e6 * PRICING_USD_PER_M_CHARS[n] for n in provider_names)


async def run_bench(
    provider_names: list[str], trials: int, concurrency: list[int]
) -> list[StreamStats]:
    results: list[StreamStats] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        providers = [PROVIDERS[name](client) for name in provider_names]

        # Warmup: one throwaway call per provider so TLS/connection setup
        # doesn't pollute the first measured trial.
        for p in providers:
            logger.info(f"warmup: {p.name}")
            await measure_stream(p, "warmup", TEXTS["short"])

        for p in providers:
            for text_id, text in TEXTS.items():
                for i in range(trials):
                    stat = await measure_stream(p, text_id, text)
                    results.append(stat)
                    logger.info(
                        f"{p.name:<12} {text_id:<16} trial {i + 1}/{trials} "
                        f"ttfb={stat.ttfb_ms}ms ok={stat.ok}"
                        + (f" err={stat.error}" if stat.error else "")
                    )
                    await asyncio.sleep(PAUSE_BETWEEN_TRIALS_S)

        for p in providers:
            for level in concurrency:
                if level <= 1:
                    continue
                logger.info(f"{p.name}: concurrency ramp x{level}")
                batch = await asyncio.gather(
                    *[
                        measure_stream(p, "medium", TEXTS["medium"], concurrency=level)
                        for _ in range(level)
                    ]
                )
                results.extend(batch)
                await asyncio.sleep(PAUSE_BETWEEN_TRIALS_S)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="TTS streaming latency benchmark")
    parser.add_argument("--providers", nargs="*", choices=sorted(PROVIDERS), default=None)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--concurrency", nargs="*", type=int, default=[1, 2, 5])
    args = parser.parse_args()

    names = args.providers or available_providers()
    if not names:
        keys = ", ".join(ENV_KEYS.values())
        raise SystemExit(f"No provider API keys found. Set at least one of: {keys} (see .env.example)")

    cost = estimate_cost_usd(names, args.trials, args.concurrency)
    logger.info(f"providers: {names} | trials: {args.trials} | concurrency: {args.concurrency}")
    logger.info(f"estimated API cost for this run: ~${cost:.2f} (see PRICING_USD_PER_M_CHARS)")

    results = asyncio.run(run_bench(names, args.trials, args.concurrency))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"bench_{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "meta": {
                    "timestamp": stamp,
                    "region": env("BENCH_REGION", "unspecified — set BENCH_REGION"),
                    "trials": args.trials,
                    "concurrency": args.concurrency,
                    "providers": names,
                },
                "results": [r.to_dict() for r in results],
            },
            indent=2,
        )
    )
    ok = sum(1 for r in results if r.ok)
    logger.info(f"done: {ok}/{len(results)} trials ok → {out}")
    logger.info("next: uv run switchboard-report")


if __name__ == "__main__":
    main()
