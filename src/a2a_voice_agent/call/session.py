"""What a call's tools write during the conversation, read back once the pipeline has drained."""

from dataclasses import dataclass
from enum import StrEnum

from loguru import logger

from a2a_voice_agent.contract import Disposition, Outcome, Result


class EndReason(StrEnum):
    """Why the agent hung up."""

    CONCLUDED = "concluded"
    CALLEE_REQUESTED_CALL_BACK = "callee_requested_call_back"
    CALLEE_UNAVAILABLE = "callee_unavailable"


@dataclass
class CallSession:
    """Shared state for one call, handed to every tool handler as the worker's app resources.

    report_outcome records the Outcome and the reason for ending; the runtime records whether the
    Callee's side of the transport ever connected.
    """

    outcome: Outcome | None = None
    end_reason: EndReason | None = None
    answered: bool = False

    def final_outcome(self) -> Outcome:
        """The Outcome to return from the call.

        The reported Outcome, with the reason for hanging up added to ``details``. A call that
        ended with no Outcome reported returns ``undetermined``: ``connected`` if the Callee's side
        of the transport connected, ``no_answer`` if it never did.
        """
        if self.outcome is None:
            logger.error(
                "Call ended with no Outcome reported (end reason: {}); returning undetermined",
                self.end_reason or "none",
            )
            return Outcome(
                disposition=Disposition.CONNECTED if self.answered else Disposition.NO_ANSWER,
                result=Result.UNDETERMINED,
                summary="The call ended without the agent reporting an outcome.",
                details={"outcome_reported": False, **self._end_reason_details()},
            )
        if not self.end_reason:
            return self.outcome
        details = {**self.outcome.details, **self._end_reason_details()}
        return self.outcome.model_copy(update={"details": details})

    def _end_reason_details(self) -> dict[str, str]:
        return {"end_reason": self.end_reason.value} if self.end_reason else {}
