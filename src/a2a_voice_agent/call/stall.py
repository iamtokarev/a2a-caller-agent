"""The Stall: holding the line while the Principal is asked a question."""

import asyncio
import time
from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from a2a_voice_agent.call.prompt import StallLines
from a2a_voice_agent.contract import Escalation, EscalationAnswer

# How the conversation layer asks the Principal a question it has no authority to answer.
EscalationHandler = Callable[[Escalation], Awaitable[EscalationAnswer | None]]

# The check-in is skipped if anything happened this recently: a live exchange needs no reassurance.
CHECK_IN_QUIET_SECS = 10.0

# What the check-in must not talk over, by the frames that start and stop each activity.
_STARTS: dict[type[Frame], str] = {
    UserStartedSpeakingFrame: "callee speaking",
    BotStartedSpeakingFrame: "agent speaking",
    LLMFullResponseStartFrame: "agent replying",
}
_STOPS: dict[type[Frame], str] = {
    UserStoppedSpeakingFrame: "callee speaking",
    BotStoppedSpeakingFrame: "agent speaking",
    LLMFullResponseEndFrame: "agent replying",
    InterruptionFrame: "agent replying",
}


class Stall(FrameProcessor):
    """Hold the line while the Principal is asked, filling it with fixed speech.

    Sits between the LLM and TTS: it speaks straight to TTS, and watches the conversation pass
    through so the check-in never talks over the Callee, the agent, or a reply on its way. The
    Callee's speech and the LLM's responses pass downstream; the agent's speech passes upstream
    from the output transport.
    """

    def __init__(
        self,
        escalate: EscalationHandler,
        budget_secs: float,
        lines: StallLines,
        quiet_secs: float = CHECK_IN_QUIET_SECS,
    ) -> None:
        super().__init__()
        self._escalate = escalate
        self._budget_secs = budget_secs
        self._lines = lines
        self._quiet_secs = quiet_secs
        self._active: set[str] = set()
        self._last_active_at: float | None = None

    async def hold(self, escalation: Escalation) -> EscalationAnswer | None:
        """Ask the Principal ``escalation`` while the Callee holds.

        Returns the answer, or None if none came within the stall budget, however that happened:
        no answer, a failed escalation handler, or the budget running out. The handler is
        cancelled at the budget; a late answer belongs to a Call-back, not to this call.
        """
        await self._say(self._lines.acknowledgement)
        check_in = self.create_task(self._check_in())
        try:
            return await asyncio.wait_for(self._escalate(escalation), self._budget_secs)
        except TimeoutError:
            logger.warning("No answer within the {}s stall budget", self._budget_secs)
        except Exception:
            logger.exception("The escalation handler failed; treating it as no answer")
        finally:
            await self.cancel_task(check_in)
        return None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        if activity := _STARTS.get(type(frame)):
            self._active.add(activity)
        elif activity := _STOPS.get(type(frame)):
            self._active.discard(activity)
        else:
            return
        self._last_active_at = time.monotonic()

    def _quiet(self) -> bool:
        """Whether nothing is going on in the conversation, and nothing has for a while."""
        if self._active:
            return False
        return (
            self._last_active_at is None
            or time.monotonic() - self._last_active_at >= self._quiet_secs
        )

    async def _check_in(self) -> None:
        await asyncio.sleep(self._budget_secs / 2)
        if self._quiet():
            await self._say(self._lines.check_in)

    async def _say(self, text: str) -> None:
        await self.push_frame(TTSSpeakFrame(text), FrameDirection.DOWNSTREAM)
