"""A call returns the reported Outcome, or undetermined when none was reported, with the
Disposition the runtime observed."""

from a2a_voice_agent.call.session import CallSession, EndReason
from a2a_voice_agent.contract import Disposition, Escalation, Outcome, Result

BOOKED = Outcome(
    disposition=Disposition.CONNECTED,
    result=Result.ACHIEVED,
    summary="Table for four booked for Saturday at 19:00.",
    details={"time": "19:00"},
)


def test_a_call_with_no_outcome_reported_is_undetermined() -> None:
    outcome = CallSession(end_reason=EndReason.CONCLUDED).final_outcome()

    assert outcome.result is Result.UNDETERMINED


def test_a_call_with_nothing_recorded_is_undetermined() -> None:
    outcome = CallSession().final_outcome()

    assert outcome.result is Result.UNDETERMINED


def test_an_unreported_call_the_callee_answered_is_connected() -> None:
    assert CallSession(answered=True).final_outcome().disposition is Disposition.CONNECTED


def test_an_unreported_call_the_callee_never_answered_is_no_answer() -> None:
    assert CallSession(answered=False).final_outcome().disposition is Disposition.NO_ANSWER


def test_the_reported_outcome_is_returned_with_the_reason_for_hanging_up() -> None:
    session = CallSession(outcome=BOOKED, end_reason=EndReason.CALLEE_REQUESTED_CALL_BACK)

    outcome = session.final_outcome()

    assert outcome.result is Result.ACHIEVED
    assert outcome.details == {"time": "19:00", "end_reason": "callee_requested_call_back"}


def test_a_call_cut_off_at_the_cap_with_nothing_reported_is_undetermined() -> None:
    outcome = CallSession(answered=True, cap_reached=True).final_outcome()

    assert outcome.result is Result.UNDETERMINED
    assert outcome.details["cap_reached"] is True


def test_a_call_cut_off_at_the_cap_keeps_the_reported_outcome() -> None:
    session = CallSession(outcome=BOOKED, end_reason=EndReason.CONCLUDED, cap_reached=True)

    outcome = session.final_outcome()

    assert outcome.result is Result.ACHIEVED
    assert outcome.details["cap_reached"] is True


def test_an_escalation_still_open_at_hang_up_is_recorded_in_the_outcome(
    booking_question: Escalation,
) -> None:
    session = CallSession(
        outcome=BOOKED,
        end_reason=EndReason.ESCALATION_UNANSWERED,
        pending_escalation=booking_question,
    )

    details = session.final_outcome().details

    assert details["pending_escalation"] == {
        "question": "The only table on Saturday is at 21:00. Is that acceptable?",
        "options": ["Take 21:00", "Decline"],
    }
    assert details["end_reason"] == "escalation_unanswered"


def test_a_callee_hanging_up_mid_stall_still_records_the_open_question(
    booking_question: Escalation,
) -> None:
    session = CallSession(answered=True, pending_escalation=booking_question)

    outcome = session.final_outcome()

    assert outcome.result is Result.UNDETERMINED
    assert outcome.details["pending_escalation"]["question"] == booking_question.question
