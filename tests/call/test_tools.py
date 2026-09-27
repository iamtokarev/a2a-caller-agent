"""report_outcome records the Outcome and the reason for ending onto the session, only for a
reachable Disposition. ask_principal opens one Escalation at a time, holds it open on the session,
and hands the model either the Principal's answer or an instruction to offer a Call-back."""

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any, cast

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import Frame, FunctionCallResultProperties
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams

from a2a_voice_agent.call.session import CallSession, EndReason
from a2a_voice_agent.call.stall import EscalationHandler
from a2a_voice_agent.call.tools import call_tools
from a2a_voice_agent.contract import (
    Disposition,
    Escalation,
    EscalationAnswer,
    Outcome,
    Result,
)

DECLINED = {
    "disposition": "connected",
    "result": "not_achieved",
    "summary": "The restaurant is fully booked on Saturday evening.",
    "details": {"alternative_offered": "Sunday at 19:00"},
    "end_reason": "concluded",
}


class _Llm:
    """Stands in for the LLM service, recording what a tool pushes into the pipeline."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def push_frame(self, frame: Frame, direction: Any = None) -> None:
        self._events.append(frame)


def _call(
    tool: FunctionSchema,
    session: CallSession,
    arguments: dict[str, Any],
    cancel_after: float | None = None,
) -> list[Any]:
    """Call ``tool`` as the LLM would; return its results and pushed frames, in order.

    With ``cancel_after``, the call is cancelled that many seconds in, as tearing down the pipeline
    does.
    """
    events: list[Any] = []

    async def result_callback(
        result: Any, *, properties: FunctionCallResultProperties | None = None
    ) -> None:
        events.append(result)

    params = FunctionCallParams(
        function_name=tool.name,
        tool_call_id="call-1",
        arguments=arguments,
        llm=cast(Any, _Llm(events)),
        pipeline_worker=cast(Any, None),
        context=LLMContext(),
        result_callback=result_callback,
        app_resources=session,
    )
    handler = tool.handler
    assert handler is not None

    async def run() -> None:
        call = asyncio.ensure_future(handler(params))
        if cancel_after is not None:
            await asyncio.sleep(cancel_after)
            call.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await call

    asyncio.run(run())
    return events


async def _unused_hold(escalation: Escalation) -> EscalationAnswer | None:
    raise AssertionError("report_outcome never asks the Principal")


def _tool(name: str, hold: EscalationHandler = _unused_hold) -> FunctionSchema:
    """The tool the model sees under ``name``, from the call's tool set."""
    [tool] = [tool for tool in call_tools(hold).standard_tools if tool.name == name]
    return tool


def _report(session: CallSession, arguments: dict[str, Any]) -> list[Any]:
    return _call(_tool("report_outcome"), session, arguments)


def test_report_outcome_records_onto_the_session() -> None:
    session = CallSession()

    _report(session, DECLINED)

    assert session.outcome == Outcome(
        disposition=Disposition.CONNECTED,
        result=Result.NOT_ACHIEVED,
        summary="The restaurant is fully booked on Saturday evening.",
        details={"alternative_offered": "Sunday at 19:00"},
    )
    assert session.end_reason is EndReason.CONCLUDED


def test_the_schema_offers_only_conversation_reachable_dispositions() -> None:
    offered = _tool("report_outcome").properties["disposition"]["enum"]

    assert set(offered) == {"connected", "voicemail", "wrong_number"}


@pytest.mark.parametrize("disposition", ["no_answer", "dial_failed", "hung_up"])
def test_report_outcome_rejects_an_unreachable_disposition(disposition: str) -> None:
    session = CallSession()

    results = _report(session, {**DECLINED, "disposition": disposition})

    assert session.outcome is None
    assert "error" in results[0]


def test_report_outcome_rejects_an_unknown_field() -> None:
    session = CallSession()

    results = _report(session, {**DECLINED, "transcript": "Hello?"})

    assert session.outcome is None
    assert "error" in results[0]


@pytest.mark.parametrize("end_reason", [None, "hung_up"])
def test_report_outcome_rejects_a_missing_or_unknown_end_reason(end_reason: str | None) -> None:
    session = CallSession()
    arguments = {k: v for k, v in DECLINED.items() if k != "end_reason"}
    if end_reason is not None:
        arguments["end_reason"] = end_reason

    results = _report(session, arguments)

    assert session.outcome is None
    assert "error" in results[0]


HUNG_UP_ON = Outcome(
    disposition=Disposition.CONNECTED,
    result=Result.UNDETERMINED,
    summary="The Callee had to go before the question was answered.",
)

Answering = Callable[[str | None], EscalationHandler]


def _ask(
    hold: EscalationHandler,
    arguments: Escalation | dict[str, Any],
    session: CallSession | None = None,
    cancel_after: float | None = None,
) -> tuple[list[dict[str, Any]], CallSession]:
    """Call ask_principal with ``arguments`` as the model would; return its results."""
    if isinstance(arguments, Escalation):
        arguments = arguments.model_dump()
    session = session or CallSession()
    events = _call(_tool("ask_principal", hold), session, arguments, cancel_after)
    return [e for e in events if isinstance(e, dict)], session


def test_the_stall_holds_the_question_with_its_options(
    answering: Answering, asked: list[Escalation], booking_question: Escalation
) -> None:
    _ask(answering("Yes, 21:00 is fine."), booking_question)

    assert asked == [booking_question]


def test_the_answer_is_handed_to_the_model_and_closes_the_escalation(
    answering: Answering, booking_question: Escalation
) -> None:
    results, session = _ask(answering("Yes, 21:00 is fine."), booking_question)

    assert results == [{"answer": "Yes, 21:00 is fine."}]
    assert session.pending_escalation is None


def test_no_answer_offers_a_call_back_and_leaves_the_question_open(
    answering: Answering, booking_question: Escalation
) -> None:
    results, session = _ask(answering(None), booking_question)

    [result] = results
    assert "answer" not in result
    assert "escalation_unanswered" in result["instruction"]
    assert session.pending_escalation == booking_question


def test_a_second_escalation_is_refused_while_one_is_open_or_went_unanswered(
    answering: Answering, asked: list[Escalation], booking_question: Escalation
) -> None:
    session = CallSession(pending_escalation=Escalation(question="Is 20:00 acceptable?"))

    [result], _ = _ask(answering("Yes."), booking_question, session)

    assert "error" in result
    assert asked == []


def test_a_question_with_no_text_is_refused(answering: Answering, asked: list[Escalation]) -> None:
    [result], session = _ask(answering("Yes."), {"question": ""})

    assert "error" in result
    assert asked == []
    assert session.pending_escalation is None


def test_an_answer_after_the_outcome_is_reported_is_not_handed_to_the_model(
    booking_question: Escalation,
) -> None:
    session = CallSession()

    async def answer_after_goodbye(escalation: Escalation) -> EscalationAnswer | None:
        session.outcome = HUNG_UP_ON
        return EscalationAnswer(text="Yes.")

    results, _ = _ask(answer_after_goodbye, booking_question, session)

    assert results == []
    assert session.pending_escalation == booking_question


def test_a_call_torn_down_mid_stall_keeps_the_question_open(
    never_answering: EscalationHandler, booking_question: Escalation
) -> None:
    results, session = _ask(never_answering, booking_question, cancel_after=0.05)

    assert results == []
    assert session.pending_escalation == booking_question
