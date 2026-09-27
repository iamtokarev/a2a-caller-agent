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
    Callee's side of the transport ever connected, and whether the call was cut off at the cap.
    """

    outcome: Outcome | None = None
    end_reason: EndReason | None = None
    answered: bool = False
    cap_reached: bool = False

    def final_outcome(self) -> Outcome:
        """The Outcome to return from the call.

        The reported Outcome, with the reason for hanging up and whether the call was cut off at
        the cap added to ``details``. A call that ended with no Outcome reported returns
        ``undetermined``: ``connected`` if the Callee's side of the transport connected,
        ``no_answer`` if it never did.
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
                details={"outcome_reported": False, **self._ending_details()},
            )
        ending_details = self._ending_details()
        if not ending_details:
            return self.outcome
        details = {**self.outcome.details, **ending_details}
        return self.outcome.model_copy(update={"details": details})

    def _ending_details(self) -> dict[str, str | bool]:
        details: dict[str, str | bool] = {}
        if self.end_reason:
            details["end_reason"] = self.end_reason.value
        if self.cap_reached:
            details["cap_reached"] = True
        return details
