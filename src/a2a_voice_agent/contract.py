"""The Brief / Outcome contract between a Client and the voice agent."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

import phonenumbers
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pydantic_extra_types.phone_numbers import PhoneNumberValidator

# The languages the pipeline actually supports.
Language = Literal["cs", "en"]


E164Number = Annotated[str | phonenumbers.PhoneNumber, PhoneNumberValidator(number_format="E164")]


class _Contract(BaseModel):
    """Shared configuration for every type that crosses the Client boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Disposition(StrEnum):
    """What happened to the phone call itself, independent of the errand.

    Only ``CONNECTED``, ``VOICEMAIL`` and ``WRONG_NUMBER`` are reachable from inside a
    conversation; the other two are the runtime's to report. The tool schema narrows to the
    first three — that narrowing lives with the tools, not here.
    """

    CONNECTED = "connected"
    VOICEMAIL = "voicemail"
    NO_ANSWER = "no_answer"
    WRONG_NUMBER = "wrong_number"
    DIAL_FAILED = "dial_failed"


class Result(StrEnum):
    """Whether the Objective was achieved, independent of what the phone call did.

    A Callee who answers and declines is a successful Disposition with a negative Result.
    """

    ACHIEVED = "achieved"
    NOT_ACHIEVED = "not_achieved"
    UNDETERMINED = "undetermined"


class Callee(_Contract):
    """The person or business that answers the phone."""

    name: str = Field(min_length=1, description="Human-readable name, spoken aloud in the call")
    number: E164Number


class Constraint(_Contract):
    """A condition the Objective must satisfy, marked negotiable or not."""

    description: str = Field(min_length=1)
    negotiable: bool


class Principal(_Contract):
    """The human on whose behalf the call is made, and who is named in the Disclosure."""

    name: str = Field(min_length=1)
    contact_phone: E164Number | None = None


class Brief(_Contract):
    """Everything a Client hands over to have one call placed."""

    objective: str = Field(
        min_length=1,
        description="What the call is meant to achieve, in free text. Exactly one per Brief.",
    )
    callee: Callee
    constraints: list[Constraint] = Field(default_factory=list)
    principal: Principal
    language: Language
    useful_until: AwareDatetime | None = Field(
        default=None,
        description=(
            "When the errand goes stale. Checked before placing a Call-back. Must carry a "
            "timezone: it is compared against the current time, and a naive value raises."
        ),
    )


class Outcome(_Contract):
    """The structured result returned when a call ends. Data the Client can act on."""

    disposition: Disposition
    result: Result
    summary: str = Field(description="Prose. The only narrative the Client receives.")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Open map for structured extras — an alternative offered, a pending question.",
    )


class Escalation(_Contract):
    """A question the agent has no authority to answer, put to the Client mid-call."""

    question: str = Field(min_length=1)
    options: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Enumerated choices, whenever the agent can enumerate them — an empty list is "
            "meaningless, so absent means 'free text only'."
        ),
    )


class EscalationAnswer(_Contract):
    """The Client's reply to an Escalation: a chosen option, or free text."""

    text: str = Field(min_length=1)
    chosen_option_index: int | None = Field(default=None, ge=0)


def parse_datetime(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, rejecting one with no timezone offset."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must carry a timezone offset: {value!r}")
    return parsed
