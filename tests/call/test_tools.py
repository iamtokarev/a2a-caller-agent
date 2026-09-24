"""report_outcome records the Outcome and the reason for ending onto the session, only for a
reachable Disposition."""

import asyncio
from typing import Any, cast

import pytest
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import Frame, FunctionCallResultProperties
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams

from a2a_voice_agent.call.session import CallSession, EndReason
from a2a_voice_agent.call.tools import REPORT_OUTCOME
from a2a_voice_agent.contract import Disposition, Outcome, Result

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


def _call(tool: FunctionSchema, session: CallSession, arguments: dict[str, Any]) -> list[Any]:
    """Call ``tool`` as the LLM would; return its results and pushed frames, in order."""
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
        await handler(params)

    asyncio.run(run())
    return events


def _report(session: CallSession, arguments: dict[str, Any]) -> list[Any]:
    return _call(REPORT_OUTCOME, session, arguments)


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
    offered = REPORT_OUTCOME.properties["disposition"]["enum"]

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
