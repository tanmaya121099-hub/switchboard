"""Cost estimation and report generation on synthetic bench data."""

from switchboard.bench.providers import PRICING_USD_PER_M_CHARS
from switchboard.bench.report import build_report
from switchboard.bench.runner import estimate_cost_usd
from switchboard.bench.texts import TEXTS


def test_cost_estimate_matches_hand_math():
    trials = 5
    concurrency = [1, 2, 5]
    seq_chars = sum(len(t) for t in TEXTS.values()) * trials
    conc_chars = len(TEXTS["medium"]) * (2 + 5)
    expected = (seq_chars + conc_chars) / 1e6 * PRICING_USD_PER_M_CHARS["cartesia"]
    assert estimate_cost_usd(["cartesia"], trials, concurrency) == expected


def test_cost_estimate_sums_providers():
    single = estimate_cost_usd(["openai"], 5, [1])
    both = estimate_cost_usd(["openai", "deepgram"], 5, [1])
    # openai and deepgram share the same list price
    assert both == 2 * single


def synthetic_run():
    def row(provider, text_id, ttfb, concurrency=1, ok=True):
        return {
            "provider": provider,
            "text_id": text_id,
            "concurrency": concurrency,
            "ok": ok,
            "error": None if ok else "HTTP 500",
            "ttfb_ms": ttfb if ok else None,
            "total_ms": 1000.0 if ok else None,
            "audio_s": 2.0 if ok else None,
            "rtf": 0.5 if ok else None,
            "n_chunks": 10,
            "n_bytes": 64000,
            "max_gap_ms": 40.0 if ok else None,
            "started_at": "2026-01-01T00:00:00+00:00",
        }

    results = []
    for text_id in ("short", "hinglish_medium"):
        results += [row("cartesia", text_id, 150.0), row("openai", text_id, 900.0)]
    results.append(row("openai", "short", None, ok=False))
    results += [row("cartesia", "medium", 400.0, concurrency=5)]
    return {
        "meta": {
            "timestamp": "20260101_000000",
            "region": "test-region",
            "trials": 2,
            "concurrency": [1, 5],
            "providers": ["cartesia", "openai"],
        },
        "results": results,
    }


def test_report_ranks_fastest_first_and_counts_errors():
    md, _table = build_report(synthetic_run())
    assert md.index("| cartesia |") < md.index("| openai |")
    assert "**cartesia > openai**" in md
    assert "1/3" in md  # openai: 1 error in 3 sequential trials


def test_report_has_hinglish_and_concurrency_sections():
    md, _table = build_report(synthetic_run())
    assert "## Hinglish (code-switched) TTFB" in md
    assert "## TTFB p95 under concurrency" in md
    assert "test-region" in md
