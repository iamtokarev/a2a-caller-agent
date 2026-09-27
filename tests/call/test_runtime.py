"""The Disclosure opens the agent's first response, once, and the context records it that way;
the call hangs up after the response that follows the Outcome; a call is warned before the cap
and cancelled at it."""

import asyncio
from collections.abc import Sequence

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelWorkerFrame,
    EndWorkerFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.tests.utils import SleepFrame, run_test

from a2a_voice_agent.call.runtime import (
    _CallCap,
    _DisclosureOnFirstResponse,
    _HangUpAfterGoodbye,
)
from a2a_voice_agent.call.session import CallSession
from a2a_voice_agent.contract import Disposition, Outcome, Result

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


BOOKED = Outcome(disposition=Disposition.CONNECTED, result=Result.ACHIEVED, summary="Table booked.")


def _pushed_by_hang_up(session: CallSession, frames: list[Frame]) -> Sequence[Frame]:
    down, _ = asyncio.run(
        run_test(_HangUpAfterGoodbye(session), frames_to_send=frames, start_timeout=30)
    )
    return down


def test_the_call_hangs_up_right_behind_the_goodbye() -> None:
    down = _pushed_by_hang_up(CallSession(outcome=BOOKED), _response("Thank you, goodbye."))

    assert isinstance(down[-1], EndWorkerFrame)
    assert isinstance(down[-2], LLMFullResponseEndFrame)


def test_the_call_stays_up_until_an_outcome_is_reported() -> None:
    down = _pushed_by_hang_up(CallSession(), _response("For four people, please."))

    assert not any(isinstance(frame, EndWorkerFrame) for frame in down)


def _pushed_by_call_cap(session: CallSession) -> list[type[Frame]]:
    """Run a call past a short cap and return the cap's own frames, in order."""
    call_cap = _CallCap(session, cap_secs=0.1, warning_lead_secs=0.05)
    down, _ = asyncio.run(
        run_test(call_cap, frames_to_send=[SleepFrame(sleep=0.2)], start_timeout=30)
    )
    return [type(f) for f in down if isinstance(f, LLMMessagesAppendFrame | CancelWorkerFrame)]


def test_the_agent_is_told_to_wrap_up_before_the_call_is_cancelled_at_the_cap() -> None:
    session = CallSession()

    assert _pushed_by_call_cap(session) == [LLMMessagesAppendFrame, CancelWorkerFrame]
    assert session.cap_reached


def test_the_call_is_cancelled_at_the_cap_even_after_an_outcome_is_reported() -> None:
    assert _pushed_by_call_cap(CallSession(outcome=BOOKED)) == [CancelWorkerFrame]
