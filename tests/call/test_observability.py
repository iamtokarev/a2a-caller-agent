"""A traced call gets a recorder bound to its conversation; tracing installs once per process."""

from collections.abc import Callable, Iterator

import pytest
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from a2a_voice_agent.call import observability
from a2a_voice_agent.call.config import CallConfig, Environment
from a2a_voice_agent.call.observability import trace_attributes, trace_call


class _FakeSpanProcessor:
    """Stands in for LangSmith's processor, remembering which recorder serves which call."""

    def __init__(self) -> None:
        self.recorders: dict[str, AudioBufferProcessor] = {}

    def attach_audio_buffer(self, audio_buffer: AudioBufferProcessor, conversation_id: str) -> None:
        self.recorders[conversation_id] = audio_buffer


@pytest.fixture
def installs(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[_FakeSpanProcessor]]:
    """Replace the LangSmith install with a fake, recording each install it performs."""
    made: list[_FakeSpanProcessor] = []

    def _configure() -> _FakeSpanProcessor:
        made.append(_FakeSpanProcessor())
        return made[-1]

    monkeypatch.setattr(observability, "configure_pipecat", _configure)
    observability._span_processor.cache_clear()
    yield made
    observability._span_processor.cache_clear()


def test_untraced_call_gets_no_recorder_and_installs_nothing(
    make_config: Callable[..., CallConfig], installs: list[_FakeSpanProcessor]
) -> None:
    assert trace_call(make_config(tracing=False), "call-1") is None
    assert installs == []


def test_traced_call_gets_a_recorder_bound_to_its_conversation(
    make_config: Callable[..., CallConfig], installs: list[_FakeSpanProcessor]
) -> None:
    recorder = trace_call(make_config(tracing=True), "call-1")

    assert recorder is not None
    assert installs[0].recorders == {"call-1": recorder}


def test_tracing_is_installed_once_across_calls(
    make_config: Callable[..., CallConfig], installs: list[_FakeSpanProcessor]
) -> None:
    config = make_config(tracing=True)

    first = trace_call(config, "call-1")
    second = trace_call(config, "call-2")

    assert len(installs) == 1
    assert installs[0].recorders == {"call-1": first, "call-2": second}


@pytest.mark.parametrize("environment", list(Environment))
def test_trace_is_tagged_with_its_environment(
    make_config: Callable[..., CallConfig], environment: Environment
) -> None:
    attributes = trace_attributes(make_config(environment=environment))

    assert attributes["langsmith.span.tags"] == environment.value
