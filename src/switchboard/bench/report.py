"""Turn raw bench JSON into the report you publish and the router consumes.

Usage:
    uv run switchboard-report                # latest results/bench_*.json
    uv run switchboard-report results/bench_20260709_120000.json
"""

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from switchboard.bench.metrics import percentile
from switchboard.bench.providers import PRICING_USD_PER_M_CHARS
from switchboard.bench.texts import HINGLISH_IDS
from switchboard.config import RESULTS_DIR


def latest_bench_file() -> Path:
    files = sorted(RESULTS_DIR.glob("bench_*.json"))
    if not files:
        raise SystemExit("No bench results found — run `uv run switchboard-bench` first")
    return files[-1]


def _fmt(v: float | None, suffix: str = "") -> str:
    return f"{v:.0f}{suffix}" if v is not None else "—"


def aggregate(rows: list[dict]) -> dict:
    ttfbs = [r["ttfb_ms"] for r in rows if r["ok"]]
    rtfs = [r["rtf"] for r in rows if r["ok"]]
    gaps = [r["max_gap_ms"] for r in rows if r["ok"] and r["max_gap_ms"] is not None]
    return {
        "n": len(rows),
        "errors": sum(1 for r in rows if not r["ok"]),
        "ttfb_p50": percentile(ttfbs, 50),
        "ttfb_p95": percentile(ttfbs, 95),
        "rtf_p50": percentile(rtfs, 50),
        "max_gap_p95": percentile(gaps, 95),
    }


def build_report(data: dict) -> tuple[str, Table]:
    meta = data["meta"]
    rows = data["results"]
    providers = meta["providers"]

    seq = [r for r in rows if r["concurrency"] == 1 and r["text_id"] != "warmup"]
    by_provider = {p: aggregate([r for r in seq if r["provider"] == p]) for p in providers}
    ranked = sorted(
        providers, key=lambda p: by_provider[p]["ttfb_p95"] if by_provider[p]["ttfb_p95"] else 1e9
    )

    table = Table(title=f"TTS streaming latency — {meta['region']} ({meta['timestamp']} UTC)")
    columns = ("provider", "TTFB p50", "TTFB p95", "RTF p50", "max gap p95", "errors", "$/1M chars")
    for col in columns:
        table.add_column(col)

    md = [
        f"# TTS provider benchmark — {meta['region']}",
        "",
        f"Run `{meta['timestamp']}` UTC · {meta['trials']} trials per text · "
        f"concurrency levels {meta['concurrency']} · raw PCM over streaming HTTP.",
        "",
        "TTFB = time to first audio byte, the silence a caller hears before the agent speaks.",
        "",
        "| Provider | TTFB p50 | TTFB p95 | RTF p50 | Max gap p95 | Errors | $/1M chars* |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in ranked:
        a = by_provider[p]
        price = PRICING_USD_PER_M_CHARS.get(p)
        cells = (
            _fmt(a["ttfb_p50"], "ms"),
            _fmt(a["ttfb_p95"], "ms"),
            f"{a['rtf_p50']:.2f}" if a["rtf_p50"] is not None else "—",
            _fmt(a["max_gap_p95"], "ms"),
            f"{a['errors']}/{a['n']}",
            f"${price:.0f}" if price else "—",
        )
        table.add_row(p, *cells)
        md.append(f"| {p} | " + " | ".join(cells) + " |")

    md += ["", "\\* List pricing at time of run — verify before quoting.", ""]

    # Hinglish subsection: the differentiator nobody publishes.
    md += [
        "## Hinglish (code-switched) TTFB",
        "",
        "| Provider | TTFB p50 | TTFB p95 | Errors |",
        "|---|---|---|---|",
    ]
    for p in ranked:
        h = aggregate([r for r in seq if r["provider"] == p and r["text_id"] in HINGLISH_IDS])
        hinglish_cells = (
            _fmt(h["ttfb_p50"], "ms"),
            _fmt(h["ttfb_p95"], "ms"),
            f"{h['errors']}/{h['n']}",
        )
        md.append(f"| {p} | " + " | ".join(hinglish_cells) + " |")

    # Concurrency degradation.
    levels = [c for c in meta["concurrency"] if c > 1]
    if levels:
        header = "| Provider | " + " | ".join(f"x{c}" for c in [1] + levels) + " |"
        md += ["", "## TTFB p95 under concurrency", "", header, "|---" * (len(levels) + 2) + "|"]
        for p in ranked:
            cells = [_fmt(by_provider[p]["ttfb_p95"], "ms")]
            for c in levels:
                conc = [r for r in rows if r["provider"] == p and r["concurrency"] == c]
                cells.append(_fmt(aggregate(conc)["ttfb_p95"], "ms"))
            md.append(f"| {p} | " + " | ".join(cells) + " |")

    md += [
        "",
        "## Verdict",
        "",
        f"Ranked by sequential TTFB p95: **{' > '.join(ranked)}**.",
        "The agent's `LatencyRouter` consumes this run directly — the fastest healthy provider",
        "takes the next call, and circuit-breaks to the runner-up after repeated failures.",
        "",
        "Methodology and caveats: single vantage point, list pricing, public API tiers,",
        "no enterprise SLAs. Numbers are for relative comparison from this region.",
    ]
    return "\n".join(md) + "\n", table


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else latest_bench_file()
    data = json.loads(path.read_text())
    md, table = build_report(data)

    Console().print(table)
    out = RESULTS_DIR / "report.md"
    out.write_text(md)
    print(f"\nmarkdown report → {out}")


if __name__ == "__main__":
    main()
