"""Shared fixtures."""

import asyncio
import copy
import os
from collections.abc import Callable
from typing import Any

import pytest

from a2a_voice_agent.call.config import CallConfig
from a2a_voice_agent.call.stall import EscalationHandler
from a2a_voice_agent.contract import Brief, Escalation, EscalationAnswer

# Callee and Principal numbers are in Ofcom's range reserved for fiction.
_BRIEF_DATA: dict[str, Any] = {
    "objective": "Book a table for four people this Saturday evening, around 19:00.",
    "callee": {"name": "The Copper Kettle", "number": "+44 20 7946 0123"},
    "constraints": [
        {"description": "The booking must be for this Saturday.", "negotiable": False},
        {"description": "Any time between 18:30 and 20:00 is fine.", "negotiable": True},
    ],
    "principal": {"name": "Alex Morgan", "contact_phone": "+44 20 7946 0456"},
    "language": "en",
    "useful_until": "2026-09-19T18:00:00+01:00",
}


@pytest.fixture
def brief_data() -> dict[str, Any]:
    """A valid Brief as raw data, fresh for each test so it can be mutated."""
    return copy.deepcopy(_BRIEF_DATA)


@pytest.fixture
def make_brief(brief_data: dict[str, Any]) -> Callable[..., Brief]:
    """Build a valid Brief, replacing top-level fields with the given overrides."""

    def _make(**overrides: Any) -> Brief:
        return Brief.model_validate({**brief_data, **overrides})

    return _make


@pytest.fixture
def make_config(monkeypatch: pytest.MonkeyPatch) -> Callable[..., CallConfig]:
    """Build a CallConfig from explicit values only, ignoring .env and the shell's settings."""
    for name in list(os.environ):
        if name.startswith("CALL_") or name in ("ENVIRONMENT", "LANGSMITH_TRACING"):
            monkeypatch.delenv(name)

    def _make(**overrides: Any) -> CallConfig:
        values: dict[str, Any] = {
            "_env_file": None,
            "deepgram_api_key": "test-deepgram",
            "cartesia_api_key": "test-cartesia",
            "openrouter_api_key": "test-openrouter",
            **overrides,
        }
        return CallConfig(**values)

    return _make


# Stand-ins for the Principal's side of an Escalation.


@pytest.fixture
def booking_question() -> Escalation:
    """A question a booking call might put to the Principal."""
    return Escalation(
        question="The only table on Saturday is at 21:00. Is that acceptable?",
        options=["Take 21:00", "Decline"],
    )


@pytest.fixture
def asked() -> list[Escalation]:
    """Every question put to a handler made by ``answering``, in order."""
    return []


@pytest.fixture
def answering(asked: list[Escalation]) -> Callable[[str | None], EscalationHandler]:
    """Make a handler that answers at once with the given text, or with no answer for None."""

    def make(answer: str | None) -> EscalationHandler:
        async def escalate(escalation: Escalation) -> EscalationAnswer | None:
            asked.append(escalation)
            return EscalationAnswer(text=answer) if answer else None

        return escalate

    return make


@pytest.fixture
def never_answering() -> EscalationHandler:
    """A handler that waits for ever, until it is cancelled."""

    async def escalate(escalation: Escalation) -> EscalationAnswer | None:
        await asyncio.Event().wait()
        return None

    return escalate
