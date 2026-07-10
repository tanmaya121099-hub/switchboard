"""Pipecat pipeline: Twilio media stream ⇄ STT → LLM → TTS, with routing.

Pinned against pipecat-ai 0.0.6x. Pipecat moves fast; if an import fails,
diff against the official twilio-chatbot example:
https://github.com/pipecat-ai/pipecat-examples/tree/main/twilio-chatbot
"""

import asyncio
import os

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.serializers.twilio import TwilioFrameSerializer

from switchboard.agent.observability import CallTrace
from switchboard.agent.playbooks import Playbook
from switchboard.agent.router import LatencyRouter
from switchboard.config import TELEPHONY_SAMPLE_RATE, env

try:  # transport module moved between pipecat versions
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )
except ImportError:
    from pipecat.transports.network.fastapi_websocket import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )


def make_tts(provider: str):
    """Instantiate the pipecat TTS service for a router-selected provider."""
    if provider == "cartesia":
        from pipecat.services.cartesia.tts import CartesiaTTSService

        return CartesiaTTSService(
            api_key=env("CARTESIA_API_KEY"),
            voice_id=os.getenv("CARTESIA_VOICE_ID") or "a0e99841-438c-4a64-b679-ae501e7d6091",
            model=os.getenv("CARTESIA_MODEL", "sonic-3"),
        )
    if provider == "elevenlabs":
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        return ElevenLabsTTSService(
            api_key=env("ELEVENLABS_API_KEY"),
            voice_id=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
            model=os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
        )
    if provider == "openai":
        from pipecat.services.openai.tts import OpenAITTSService

        return OpenAITTSService(
            api_key=env("OPENAI_API_KEY"), voice=os.getenv("OPENAI_TTS_VOICE", "alloy")
        )
    if provider == "deepgram":
        from pipecat.services.deepgram.tts import DeepgramTTSService

        return DeepgramTTSService(
            api_key=env("DEEPGRAM_API_KEY"),
            voice=os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en"),
        )
    raise ValueError(f"unknown TTS provider: {provider}")


def make_stt():
    from pipecat.services.deepgram.stt import DeepgramSTTService

    kwargs = {"api_key": env("DEEPGRAM_API_KEY")}
    try:
        from deepgram import LiveOptions

        kwargs["live_options"] = LiveOptions(
            model=os.getenv("DEEPGRAM_STT_MODEL", "nova-3"),
            # "multi" = code-switching (Hinglish); the whole India story hinges on this.
            language=os.getenv("DEEPGRAM_STT_LANGUAGE", "multi"),
        )
    except Exception:
        logger.warning("deepgram LiveOptions unavailable — falling back to STT defaults")
    return DeepgramSTTService(**kwargs)


def make_llm():
    provider = os.getenv("LLM_PROVIDER", "anthropic")
    model = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
    if provider == "anthropic":
        from pipecat.services.anthropic.llm import AnthropicLLMService

        return AnthropicLLMService(api_key=env("ANTHROPIC_API_KEY"), model=model)
    from pipecat.services.openai.llm import OpenAILLMService

    return OpenAILLMService(api_key=env("OPENAI_API_KEY"), model=model)


def select_tts_with_failover(router: LatencyRouter, trace: CallTrace):
    """Call-setup failover: walk the ranked list until a service constructs."""
    last_error = None
    for provider in router.ranked():
        try:
            service = make_tts(provider)
            trace.event("tts_selected", provider=provider)
            return provider, service
        except Exception as exc:  # noqa: BLE001 — try the runner-up, don't drop the call
            last_error = exc
            router.record_failure(provider)
            trace.event("tts_setup_failed", provider=provider, error=str(exc))
    raise RuntimeError(f"all TTS providers failed at setup: {last_error}")


async def run_call(
    websocket, stream_sid: str, call_sid: str, playbook: Playbook, router: LatencyRouter
) -> None:
    trace = CallTrace(call_sid, playbook.name, router.snapshot())
    provider, tts = select_tts_with_failover(router, trace)
    trace.set_tts_provider(provider)

    serializer_kwargs = {"stream_sid": stream_sid, "call_sid": call_sid}
    # Account creds enable pipecat's auto-hangup when the pipeline ends.
    if os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"):
        serializer_kwargs |= {
            "account_sid": os.getenv("TWILIO_ACCOUNT_SID"),
            "auth_token": os.getenv("TWILIO_AUTH_TOKEN"),
        }

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(),
            serializer=TwilioFrameSerializer(**serializer_kwargs),
        ),
    )

    llm = make_llm()
    context = OpenAILLMContext([{"role": "system", "content": playbook.system_prompt}])
    context_aggregator = llm.create_context_aggregator(context)

    pipeline = Pipeline(
        [
            transport.input(),
            make_stt(),
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,  # barge-in: caller can cut the agent off
            audio_in_sample_rate=TELEPHONY_SAMPLE_RATE,
            audio_out_sample_rate=TELEPHONY_SAMPLE_RATE,
            enable_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(transport, client):
        trace.event("media_stream_connected")
        await task.queue_frames([TTSSpeakFrame(playbook.greeting)])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport, client):
        trace.event("media_stream_disconnected")
        await task.cancel()

    # Public-demo guard: bound the credits one call can burn.
    max_call_seconds = float(os.getenv("MAX_CALL_SECONDS", "180"))

    try:
        await asyncio.wait_for(
            PipelineRunner(handle_sigint=False).run(task), timeout=max_call_seconds
        )
        router.record_success(provider)
        trace.close("completed")
    except TimeoutError:
        router.record_success(provider)  # hitting the cap is not a provider failure
        trace.event("max_call_duration_reached", limit_s=max_call_seconds)
        trace.close("timed_out")
    except Exception as exc:
        router.record_failure(provider)
        trace.event("pipeline_error", error=str(exc))
        trace.close("errored")
        raise
