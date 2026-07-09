"""Timing capture around a streaming TTS request.

The metric that matters for conversational voice is time-to-first-byte
(TTFB): it is the silence the caller hears before the agent starts speaking.
Real-time factor (RTF) and inter-chunk gaps matter next — a stream that
starts fast but stalls mid-sentence produces audible glitches on a phone
line, which buffers only ~20 ms frames.
"""

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import perf_counter

from switchboard.config import BYTES_PER_SAMPLE


@dataclass
class StreamStats:
    provider: str
    text_id: str
    concurrency: int
    ok: bool
    error: str | None
    ttfb_ms: float | None
    total_ms: float | None
    audio_s: float | None
    rtf: float | None  # synthesis wall time / audio duration; < 1.0 = faster than realtime
    n_chunks: int
    n_bytes: int
    max_gap_ms: float | None  # longest stall between chunks after first byte
    started_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


async def measure_stream(provider, text_id: str, text: str, concurrency: int = 1) -> StreamStats:
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = perf_counter()
    ttfb = None
    last = None
    gaps: list[float] = []
    n_bytes = 0
    n_chunks = 0
    try:
        async for chunk in provider.stream(text):
            if not chunk:
                continue
            now = perf_counter()
            if ttfb is None:
                ttfb = now - t0
            else:
                gaps.append(now - last)
            last = now
            n_bytes += len(chunk)
            n_chunks += 1
        total = perf_counter() - t0
        if n_bytes == 0:
            raise RuntimeError("stream completed with zero audio bytes")
        audio_s = n_bytes / (provider.sample_rate * BYTES_PER_SAMPLE)
        return StreamStats(
            provider=provider.name,
            text_id=text_id,
            concurrency=concurrency,
            ok=True,
            error=None,
            ttfb_ms=round(ttfb * 1000, 1),
            total_ms=round(total * 1000, 1),
            audio_s=round(audio_s, 2),
            rtf=round(total / audio_s, 3),
            n_chunks=n_chunks,
            n_bytes=n_bytes,
            max_gap_ms=round(max(gaps) * 1000, 1) if gaps else None,
            started_at=started_at,
        )
    except Exception as exc:  # noqa: BLE001 — a bench trial must never kill the run
        return StreamStats(
            provider=provider.name,
            text_id=text_id,
            concurrency=concurrency,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            ttfb_ms=None,
            total_ms=None,
            audio_s=None,
            rtf=None,
            n_chunks=n_chunks,
            n_bytes=n_bytes,
            max_gap_ms=None,
            started_at=started_at,
        )
