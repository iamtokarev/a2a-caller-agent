"""Trace a call; one trace per call, holding its transcript and stereo audio."""

from functools import cache

from langsmith.integrations.pipecat import (
    PipecatLangSmithSpanProcessor,
    configure_pipecat,
    set_thread_id,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from a2a_voice_agent.call.config import CallConfig


@cache
def _span_processor() -> PipecatLangSmithSpanProcessor:
    return configure_pipecat()


def trace_call(config: CallConfig, conversation_id: str) -> AudioBufferProcessor | None:
    """Trace this call if tracing is turned on."""

    if not config.tracing:
        return None

    processor = _span_processor()

    set_thread_id(conversation_id)
    recorder = AudioBufferProcessor(num_channels=2, buffer_size=32_000)
    processor.attach_audio_buffer(recorder, conversation_id=conversation_id)
    return recorder


def trace_attributes(config: CallConfig) -> dict[str, str]:
    """Attributes for the call's root span: tags the trace with the environment it ran in."""
    return {"langsmith.span.tags": config.environment.value}
