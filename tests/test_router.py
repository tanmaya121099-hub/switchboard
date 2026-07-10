"""Router tests: ranking from bench data, env gating, circuit breaker."""

import json

import pytest

from switchboard.agent.router import (
    COOLDOWN_SECONDS,
    DEFAULT_ORDER,
    MAX_CONSECUTIVE_FAILURES,
    LatencyRouter,
)
from switchboard.bench.providers import ENV_KEYS


def write_bench(tmp_path, ttfbs_by_provider):
    """Minimal bench_*.json: 3 sequential trials per provider at the given TTFB."""
    results = [
        {"provider": p, "ok": True, "concurrency": 1, "ttfb_ms": ms}
        for p, ms in ttfbs_by_provider.items()
        for _ in range(3)
    ]
    payload = {"meta": {"providers": list(ttfbs_by_provider)}, "results": results}
    (tmp_path / "bench_20260101_000000.json").write_text(json.dumps(payload))


@pytest.fixture
def all_keys(monkeypatch):
    for key in ENV_KEYS.values():
        monkeypatch.setenv(key, "test-key")


def test_ranks_by_measured_ttfb(tmp_path, all_keys):
    write_bench(tmp_path, {"elevenlabs": 400.0, "cartesia": 150.0, "openai": 900.0})
    router = LatencyRouter(results_dir=tmp_path)
    assert router.ranked()[:3] == ["cartesia", "elevenlabs", "openai"]


def test_unmeasured_providers_rank_after_measured(tmp_path, all_keys):
    write_bench(tmp_path, {"openai": 900.0})
    router = LatencyRouter(results_dir=tmp_path)
    ranked = router.ranked()
    assert ranked[0] == "openai"
    assert set(ranked) == set(DEFAULT_ORDER)


def test_no_bench_data_falls_back_to_default_order(tmp_path, all_keys):
    router = LatencyRouter(results_dir=tmp_path)
    assert router.ranked() == DEFAULT_ORDER


def test_unconfigured_providers_excluded(tmp_path, monkeypatch):
    for key in ENV_KEYS.values():
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(ENV_KEYS["openai"], "test-key")
    router = LatencyRouter(results_dir=tmp_path)
    assert router.ranked() == ["openai"]
    assert router.pick() == "openai"


def test_pick_raises_when_nothing_available(tmp_path, monkeypatch):
    for key in ENV_KEYS.values():
        monkeypatch.delenv(key, raising=False)
    router = LatencyRouter(results_dir=tmp_path)
    with pytest.raises(RuntimeError, match="no TTS provider available"):
        router.pick()


def test_circuit_opens_after_consecutive_failures(tmp_path, all_keys):
    write_bench(tmp_path, {"cartesia": 150.0, "deepgram": 300.0})
    router = LatencyRouter(results_dir=tmp_path)
    assert router.pick() == "cartesia"
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        router.record_failure("cartesia")
    assert router.pick() == "deepgram"
    assert "cartesia" in router.snapshot()["circuits_open"]


def test_success_resets_failure_count(tmp_path, all_keys):
    router = LatencyRouter(results_dir=tmp_path)
    for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
        router.record_failure("cartesia")
    router.record_success("cartesia")
    router.record_failure("cartesia")  # would trip the breaker without the reset
    assert "cartesia" in router.ranked()


def test_circuit_closes_after_cooldown(tmp_path, all_keys, monkeypatch):
    router = LatencyRouter(results_dir=tmp_path)
    clock = {"now": 1000.0}
    monkeypatch.setattr("switchboard.agent.router.monotonic", lambda: clock["now"])
    for _ in range(MAX_CONSECUTIVE_FAILURES):
        router.record_failure("cartesia")
    assert "cartesia" not in router.ranked()
    clock["now"] += COOLDOWN_SECONDS + 1
    assert "cartesia" in router.ranked()


def test_failed_trials_excluded_from_rankings(tmp_path, all_keys):
    results = [
        {"provider": "cartesia", "ok": True, "concurrency": 1, "ttfb_ms": 150.0},
        {"provider": "cartesia", "ok": False, "concurrency": 1, "ttfb_ms": None},
        {"provider": "cartesia", "ok": True, "concurrency": 5, "ttfb_ms": 5000.0},
    ]
    payload = {"meta": {"providers": ["cartesia"]}, "results": results}
    (tmp_path / "bench_20260101_000000.json").write_text(json.dumps(payload))
    router = LatencyRouter(results_dir=tmp_path)
    # Only the ok, concurrency=1 trial counts.
    assert router.snapshot()["rankings_ttfb_p95_ms"]["cartesia"] == 150.0
