"""The agent's tools: report the Outcome, which also ends the call, and ask the Principal."""

from loguru import logger
from pipecat.adapters.schemas.direct_function import tool_options
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams
from pydantic import ValidationError

from a2a_voice_agent.call.session import CallSession, EndReason
from a2a_voice_agent.call.stall import EscalationHandler
from a2a_voice_agent.contract import Disposition, Escalation, Outcome, Result

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


_REPORT_OUTCOME = FunctionSchema(
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
                "callee_unavailable if the right person cannot come to the phone; "
                "escalation_unanswered if you asked the Principal and no answer came in time."
            ),
        },
    },
    required=["disposition", "result", "summary", "end_reason"],
    handler=_report_outcome,
)


NO_ANSWER_INSTRUCTION = (
    "No answer came in time. Tell the Callee you will call back once you have an answer, and "
    "listen to their reply. Then call report_outcome with end_reason "
    f"{EndReason.ESCALATION_UNANSWERED} and result {Result.UNDETERMINED}."
)


def _ask_principal(hold: EscalationHandler) -> FunctionSchema:
    """The tool that opens an Escalation, held by ``hold``.

    One Escalation at a time: it stays open on the session until answered, and for good if it went
    unanswered, so the Outcome carries it. The answer comes back as the tool result; no answer
    comes back as an instruction to offer a Call-back.
    """

    # The Stall outlives interruptions: the Callee talking must not abandon the question.
    @tool_options(cancel_on_interruption=False)
    async def handler(params: FunctionCallParams) -> None:
        session = _session(params)
        if session.pending_escalation is not None:
            await _reject(
                params,
                "A question to the Principal is already open or went unanswered on this call. "
                "Do not ask again.",
            )
            return
        try:
            escalation = Escalation.model_validate(params.arguments)
        except ValidationError as exc:
            await _reject(params, exc.errors(include_url=False))
            return

        session.pending_escalation = escalation
        logger.info("Escalation opened: {}", escalation.model_dump_json())
        answer = await hold(escalation)

        if session.outcome is not None:
            # The call is already saying goodbye: the question stays open for the Outcome, and
            # nothing more is handed to the model.
            logger.info("Outcome reported while the Principal was being asked; not relaying")
            return
        if answer is None:
            await params.result_callback({"instruction": NO_ANSWER_INSTRUCTION})
            return
        session.pending_escalation = None
        logger.info("Escalation answered: {}", answer.text)
        await params.result_callback({"answer": answer.text})

    return FunctionSchema(
        name="ask_principal",
        description=(
            "Ask the Principal a question you have no authority to answer, while the Callee "
            "holds. Use it only when the only way forward would break a must_hold constraint, "
            "or the Callee asks something the errand does not cover; never to bend a may_bend "
            "constraint. Call it without saying anything first: the Callee automatically hears "
            "that you are checking. Keep talking with the Callee while you wait, but agree to "
            "nothing the question covers. The answer, or an instruction if none comes, arrives "
            "later."
        ),
        properties={
            "question": {
                "type": "string",
                "description": (
                    "In English. Self-contained: the Principal has not heard the call, so say "
                    "what was offered or asked."
                ),
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The choices, in English, whenever you can list them.",
            },
        },
        required=["question"],
        handler=handler,
    )


def call_tools(hold: EscalationHandler) -> ToolsSchema:
    """Get `call` toolset"""
    return ToolsSchema(standard_tools=[_REPORT_OUTCOME, _ask_principal(hold)])
