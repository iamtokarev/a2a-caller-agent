"""The Disclosure opens the agent's first response, once, and the context records it that way."""

import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.tests.utils import SleepFrame, run_test

from a2a_voice_agent.call.runtime import _DisclosureOnFirstResponse

DISCLOSURE = "Hello, this is an AI assistant calling for Vasilii Tokarev."


def _response(text: str) -> list[Frame]:
    return [LLMFullResponseStartFrame(), LLMTextFrame(text), LLMFullResponseEndFrame()]


def _context_after(*responses: str) -> list[dict[str, str]]:
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(context)
    pipeline = Pipeline([_DisclosureOnFirstResponse(DISCLOSURE), aggregators.assistant()])
    frames = [frame for text in responses for frame in _response(text)]

    asyncio.run(run_test(pipeline, frames_to_send=frames, start_timeout=30))

    return context.get_messages()  # type: ignore[return-value]


def test_disclosure_opens_the_first_response() -> None:
    messages = _context_after("I'd like to book a table for four.")

    assert messages == [
        {"role": "assistant", "content": f"{DISCLOSURE} I'd like to book a table for four."}
    ]


def test_disclosure_is_spoken_only_once() -> None:
    messages = _context_after("I'd like to book a table for four.", "Inside, please.")

    assert sum(DISCLOSURE in m["content"] for m in messages) == 1
    assert messages[1] == {"role": "assistant", "content": "Inside, please."}


def _disclosures_pushed(frames: list[Frame]) -> int:
    down, _ = asyncio.run(
        run_test(_DisclosureOnFirstResponse(DISCLOSURE), frames_to_send=frames, start_timeout=30)
    )
    return sum(isinstance(f, LLMTextFrame) and DISCLOSURE in f.text for f in down)


def test_disclosure_is_not_repeated_once_the_bot_has_started_speaking() -> None:
    frames = [
        *_response("I'd like to book a table."),
        BotStartedSpeakingFrame(),
        *_response("Inside, please."),
    ]

    assert _disclosures_pushed(frames) == 1


def test_disclosure_is_retried_when_interrupted_before_the_bot_spoke() -> None:
    frames = [
        LLMFullResponseStartFrame(),
        SleepFrame(sleep=0.05),  # system frames jump the queue; let the response start first
        InterruptionFrame(),
        *_response("I'd like to book a table."),
    ]

    assert _disclosures_pushed(frames) == 2
