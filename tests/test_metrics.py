"""Unit tests for percentile math and stream measurement (no network)."""

import asyncio

import pytest

from switchboard.bench.metrics import measure_stream, percentile


class TestPercentile:
    def test_empty_returns_none(self):
        assert percentile([], 95) is None

    def test_single_value(self):
        assert percentile([42.0], 50) == 42.0
        assert percentile([42.0], 95) == 42.0

    def test_median_of_even_count_interpolates(self):
        assert percentile([10.0, 20.0], 50) == 15.0

    def test_p95_interpolation(self):
        values = [float(v) for v in range(1, 101)]  # 1..100
        assert percentile(values, 95) == pytest.approx(95.05)

    def test_unsorted_input(self):
        assert percentile([30.0, 10.0, 20.0], 50) == 20.0


class FakeProvider:
    """Yields canned chunks with controlled delays; mimics a TTSProvider."""

    name = "fake"
    sample_rate = 16_000

    def __init__(self, chunks, delay_s=0.0, explode=False):
        self._chunks = chunks
        self._delay_s = delay_s
        self._explode = explode

    async def stream(self, text):
        if self._explode:
            raise RuntimeError("upstream 500")
        for chunk in self._chunks:
            if self._delay_s:
                await asyncio.sleep(self._delay_s)
            yield chunk


class TestMeasureStream:
    def test_success_stats(self):
        # 32_000 bytes of s16le @ 16 kHz = exactly 1.0 s of audio
        provider = FakeProvider([b"\x00" * 16_000, b"\x00" * 16_000])
        stat = asyncio.run(measure_stream(provider, "short", "hello"))
        assert stat.ok
        assert stat.error is None
        assert stat.n_chunks == 2
        assert stat.n_bytes == 32_000
        assert stat.audio_s == 1.0
        assert stat.ttfb_ms is not None and stat.ttfb_ms >= 0
        # An instant fake stream can round to rtf 0.0 — only shape is asserted here.
        assert stat.rtf is not None and stat.rtf >= 0

    def test_empty_chunks_are_skipped_for_ttfb(self):
        provider = FakeProvider([b"", b"\x00" * 100])
        stat = asyncio.run(measure_stream(provider, "short", "hello"))
        assert stat.ok
        assert stat.n_chunks == 1

    def test_zero_bytes_is_a_failure(self):
        provider = FakeProvider([])
        stat = asyncio.run(measure_stream(provider, "short", "hello"))
        assert not stat.ok
        assert "zero audio bytes" in stat.error

    def test_provider_exception_is_captured_not_raised(self):
        provider = FakeProvider([], explode=True)
        stat = asyncio.run(measure_stream(provider, "short", "hello"))
        assert not stat.ok
        assert "RuntimeError" in stat.error
        assert stat.ttfb_ms is None

    def test_inter_chunk_gap_measured(self):
        provider = FakeProvider([b"a", b"b", b"c"], delay_s=0.02)
        stat = asyncio.run(measure_stream(provider, "short", "hello"))
        assert stat.ok
        assert stat.max_gap_ms is not None and stat.max_gap_ms >= 15
