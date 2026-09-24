"""The tool the agent calls to report the Outcome, which also ends the call."""

from loguru import logger
from pipecat.adapters.schemas.direct_function import DirectFunction, tool_options
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.llm_service import FunctionCallParams
from pydantic import ValidationError

from a2a_voice_agent.call.session import CallSession, EndReason
from a2a_voice_agent.contract import Disposition, Outcome, Result

# The Dispositions reachable from inside a conversation. Only the runtime can know a call was
# never answered or never dialled.
REPORTABLE_DISPOSITIONS = (Disposition.CONNECTED, Disposition.VOICEMAIL, Disposition.WRONG_NUMBER)


def _session(params: FunctionCallParams) -> CallSession:
    session = params.app_resources
    if not isinstance(session, CallSession):
        raise TypeError(f"expected a CallSession as app resources, got {type(session).__name__}")
    return session


async def _reject(params: FunctionCallParams, error: object) -> None:
    """Record nothing and hand the error back to the model, so it can call again."""
    logger.warning("Rejected {}: {}", params.function_name, error)
    await params.result_callback({"error": str(error)})


# The Callee talking over the tool must not cancel it, or the Outcome would be lost.
@tool_options(cancel_on_interruption=False)
async def _report_outcome(params: FunctionCallParams) -> None:
    session = _session(params)
    arguments = dict(params.arguments)
    try:
        end_reason = EndReason(arguments.pop("end_reason", None))
    except ValueError:
        await _reject(params, f"end_reason must be one of: {', '.join(EndReason)}")
        return
    try:
        outcome = Outcome.model_validate(arguments)
    except ValidationError as exc:
        await _reject(params, exc.errors(include_url=False))
        return
    if outcome.disposition not in REPORTABLE_DISPOSITIONS:
        await _reject(params, f"disposition must be one of: {', '.join(REPORTABLE_DISPOSITIONS)}")
        return

    if session.outcome is not None:
        logger.warning("report_outcome called again; replacing the earlier Outcome")
    session.outcome = outcome
    session.end_reason = end_reason
    logger.info("Outcome reported ({}): {}", end_reason.value, outcome.model_dump_json())
    # The model answers the result with its goodbye; the runtime hangs up once it is spoken.
    await params.result_callback({"recorded": True})


REPORT_OUTCOME = FunctionSchema(
    name="report_outcome",
    description=(
        "Record what happened on this call, once the errand is settled either way. Then say "
        "a short goodbye: the call hangs up by itself as soon as you have said it."
    ),
    properties={
        "disposition": {
            "type": "string",
            "enum": [d.value for d in REPORTABLE_DISPOSITIONS],
            "description": (
                "What happened to the phone call itself: connected if you spoke with someone, "
                "voicemail if you reached a voicemail, wrong_number if the Callee is not who "
                "you meant to reach."
            ),
        },
        "result": {
            "type": "string",
            "enum": [r.value for r in Result],
            "description": (
                "Whether the Objective was achieved. A Callee who answers and declines is "
                "not_achieved, not a failed call. Use undetermined only if you cannot tell."
            ),
        },
        "summary": {
            "type": "string",
            "description": "One or two plain sentences on what was agreed or why not.",
        },
        "details": {
            "type": "object",
            "description": (
                "Structured facts worth acting on, such as the agreed time, the name the "
                "booking is under, an alternative offered, or a negotiable condition you bent."
            ),
        },
        "end_reason": {
            "type": "string",
            "enum": [r.value for r in EndReason],
            "description": (
                "Why the call is ending. concluded once the conversation has reached an end, "
                "whether or not the Objective was achieved (the result says which); "
                "callee_requested_call_back if the Callee asked to be called back later; "
                "callee_unavailable if the right person cannot come to the phone."
            ),
        },
    },
    required=["disposition", "result", "summary", "end_reason"],
    handler=_report_outcome,
)

TOOLS: list[FunctionSchema | DirectFunction] = [REPORT_OUTCOME]
