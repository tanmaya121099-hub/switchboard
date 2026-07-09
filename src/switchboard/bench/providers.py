"""Streaming TTS clients for the benchmark.

Deliberately raw httpx instead of vendor SDKs: the point is to measure the
wire, and SDKs add buffering you can't see. Every provider is asked for raw
PCM so TTFB means "first audible sample", not "first container header byte".

Endpoint shapes verified against provider docs as of 2026-07. If a request
starts returning 4xx, check the provider's changelog first — these APIs move.
"""

import os
from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx

from switchboard.config import BENCH_SAMPLE_RATE, env

# USD per 1M characters, from public pricing pages as of 2026-07.
# VERIFY against current pricing pages before publishing your report —
# these change quarterly and vary by tier.
PRICING_USD_PER_M_CHARS = {
    "cartesia": 34.0,
    "elevenlabs": 75.0,
    "openai": 15.0,
    "deepgram": 15.0,
}

# Which env var gates each provider (shared with the agent's router).
ENV_KEYS = {
    "cartesia": "CARTESIA_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
}


class ProviderHTTPError(RuntimeError):
    pass


async def _raise_with_body(response: httpx.Response) -> None:
    if response.status_code >= 400:
        body = (await response.aread())[:500]
        raise ProviderHTTPError(f"HTTP {response.status_code}: {body.decode(errors='replace')}")


class TTSProvider(ABC):
    name: str
    sample_rate: int = BENCH_SAMPLE_RATE

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    @abstractmethod
    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield raw PCM chunks as they arrive off the wire."""


class CartesiaSonic(TTSProvider):
    name = "cartesia"

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        headers = {
            "Authorization": f"Bearer {env('CARTESIA_API_KEY')}",
            "Cartesia-Version": os.getenv("CARTESIA_VERSION", "2025-04-16"),
        }
        payload = {
            "model_id": os.getenv("CARTESIA_MODEL", "sonic-3"),
            "transcript": text,
            # Public sample voice as fallback; set CARTESIA_VOICE_ID from play.cartesia.ai
            "voice": {"mode": "id", "id": os.getenv("CARTESIA_VOICE_ID") or "a0e99841-438c-4a64-b679-ae501e7d6091"},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": self.sample_rate,
            },
        }
        async with self._client.stream(
            "POST", "https://api.cartesia.ai/tts/bytes", headers=headers, json=payload
        ) as r:
            await _raise_with_body(r)
            async for chunk in r.aiter_bytes():
                yield chunk


class ElevenLabsFlash(TTSProvider):
    name = "elevenlabs"

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
        async with self._client.stream(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
            params={"output_format": "pcm_16000"},
            headers={"xi-api-key": env("ELEVENLABS_API_KEY")},
            json={"text": text, "model_id": os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")},
        ) as r:
            await _raise_with_body(r)
            async for chunk in r.aiter_bytes():
                yield chunk


class OpenAITTS(TTSProvider):
    name = "openai"
    sample_rate = 24_000  # OpenAI's pcm output is fixed at 24 kHz

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        async with self._client.stream(
            "POST",
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {env('OPENAI_API_KEY')}"},
            json={
                "model": os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
                "voice": os.getenv("OPENAI_TTS_VOICE", "alloy"),
                "input": text,
                "response_format": "pcm",
            },
        ) as r:
            await _raise_with_body(r)
            async for chunk in r.aiter_bytes():
                yield chunk


class DeepgramAura(TTSProvider):
    name = "deepgram"

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        async with self._client.stream(
            "POST",
            "https://api.deepgram.com/v1/speak",
            params={
                "model": os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"),
                "encoding": "linear16",
                "sample_rate": str(self.sample_rate),
            },
            headers={"Authorization": f"Token {env('DEEPGRAM_API_KEY')}"},
            json={"text": text},
        ) as r:
            await _raise_with_body(r)
            async for chunk in r.aiter_bytes():
                yield chunk


PROVIDERS: dict[str, type[TTSProvider]] = {
    p.name: p for p in (CartesiaSonic, ElevenLabsFlash, OpenAITTS, DeepgramAura)
}


def available_providers() -> list[str]:
    """Providers whose API key is present in the environment."""
    return [name for name, key in ENV_KEYS.items() if os.getenv(key)]
