"""The Stall holds the line while the Principal is asked: it acknowledges, checks in once halfway
through a quiet hold, and returns the answer, or nothing if none came within the stall budget."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    DataFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.utils import SleepFrame, run_test

from a2a_voice_agent.call.prompt import StallLines
from a2a_voice_agent.call.stall import CHECK_IN_QUIET_SECS, EscalationHandler, Stall
from a2a_voice_agent.contract import Escalation, EscalationAnswer

LINES = StallLines(
    acknowledgement="One moment, please, I need to check that with Alex Morgan.",
    check_in="Thank you for waiting, I am still checking.",
)
QUESTION = Escalation(question="Is 21:00 acceptable?")

Answering = Callable[[str | None], EscalationHandler]


@dataclass
class _Open(DataFrame):
    """Opens the Stall once every frame before it has passed through."""


class _OpenStall(FrameProcessor):
    """Calls ``hold`` when an ``_Open`` frame arrives, recording what it returns."""

    def __init__(self, stall: Stall, escalation: Escalation) -> None:
        super().__init__()
        self._stall = stall
        self._escalation = escalation
        self.returned: list[EscalationAnswer | None] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, _Open):
            self.create_task(self._hold())
        else:
            await self.push_frame(frame, direction)

    async def _hold(self) -> None:
        self.returned.append(await self._stall.hold(self._escalation))


@dataclass
class _Held:
    returned: list[EscalationAnswer | None]
    spoken: list[str]


def _hold(
    escalate: EscalationHandler,
    budget: float,
    frames: Sequence[Frame] = (),
    quiet_secs: float = CHECK_IN_QUIET_SECS,
) -> _Held:
    """Open a hold after ``frames`` have passed through the Stall, and let it run its course."""
    stall = Stall(escalate, budget, LINES, quiet_secs)
    opener = _OpenStall(stall, QUESTION)
    down, _ = asyncio.run(
        run_test(
            Pipeline([opener, stall]),
            frames_to_send=[*frames, _Open(), SleepFrame(sleep=budget + 0.1)],
            start_timeout=30,
        )
    )
    spoken = [frame.text for frame in down if isinstance(frame, TTSSpeakFrame)]
    return _Held(returned=opener.returned, spoken=spoken)


def test_the_principal_is_asked_the_question(answering: Answering, asked: list[Escalation]) -> None:
    _hold(answering("Yes, 21:00 is fine."), budget=1.0)

    assert asked == [QUESTION]


def test_the_answer_is_returned(answering: Answering) -> None:
    held = _hold(answering("Yes, 21:00 is fine."), budget=1.0)

    assert held.returned == [EscalationAnswer(text="Yes, 21:00 is fine.")]


def test_the_callee_hears_the_acknowledgement_at_once(answering: Answering) -> None:
    held = _hold(answering("Yes."), budget=1.0)

    assert held.spoken == [LINES.acknowledgement]


def test_no_answer_returns_nothing(answering: Answering) -> None:
    held = _hold(answering(None), budget=1.0)

    assert held.returned == [None]


def test_an_answer_that_misses_the_stall_budget_returns_nothing(
    never_answering: EscalationHandler,
) -> None:
    held = _hold(never_answering, budget=0.05)

    assert held.returned == [None]


def test_a_failing_escalation_handler_returns_nothing() -> None:
    async def broken(escalation: Escalation) -> EscalationAnswer | None:
        raise ConnectionError("terminal went away")

    held = _hold(broken, budget=1.0)

    assert held.returned == [None]


def test_the_principal_is_no_longer_waited_on_once_the_budget_is_spent() -> None:
    cancelled: list[bool] = []

    async def slow(escalation: Escalation) -> EscalationAnswer | None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return None

    _hold(slow, budget=0.05)

    assert cancelled == [True]


def test_a_check_in_is_spoken_halfway_through_a_quiet_hold(
    never_answering: EscalationHandler,
) -> None:
    held = _hold(never_answering, budget=0.2)

    assert held.spoken == [LINES.acknowledgement, LINES.check_in]


def test_no_check_in_when_the_answer_comes_first(answering: Answering) -> None:
    held = _hold(answering("Yes."), budget=0.2)

    assert held.spoken == [LINES.acknowledgement]


@pytest.mark.parametrize(
    "activity",
    [UserStartedSpeakingFrame(), BotStartedSpeakingFrame(), LLMFullResponseStartFrame()],
    ids=["callee_speaking", "agent_speaking", "agent_replying"],
)
def test_no_check_in_while_someone_is_speaking_or_a_reply_is_on_its_way(
    activity: Frame, never_answering: EscalationHandler
) -> None:
    held = _hold(never_answering, budget=0.2, frames=[activity])

    assert held.spoken == [LINES.acknowledgement]


def test_no_check_in_when_someone_spoke_moments_ago(never_answering: EscalationHandler) -> None:
    frames = [UserStartedSpeakingFrame(), SleepFrame(sleep=0.05), UserStoppedSpeakingFrame()]

    held = _hold(never_answering, budget=0.2, frames=frames)

    assert held.spoken == [LINES.acknowledgement]


def test_an_interrupted_reply_no_longer_holds_back_the_check_in(
    never_answering: EscalationHandler,
) -> None:
    frames = [LLMFullResponseStartFrame(), SleepFrame(sleep=0.05), InterruptionFrame()]

    held = _hold(never_answering, budget=0.2, frames=frames, quiet_secs=0)

    assert held.spoken == [LINES.acknowledgement, LINES.check_in]
