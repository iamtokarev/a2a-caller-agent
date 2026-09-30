"""Shared fixtures."""

import asyncio
import copy
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

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


TESTS_DIR = Path(__file__).resolve().parent

# The API key variables the tests' own services file names, set to fakes for every test.
SERVICE_KEYS = {
    "OPENROUTER_API_KEY": "test-openrouter",
    "DEEPGRAM_API_KEY": "test-deepgram",
    "CARTESIA_API_KEY": "test-cartesia",
}


@pytest.fixture
def make_config(monkeypatch: pytest.MonkeyPatch) -> Callable[..., CallConfig]:
    """Build a CallConfig from explicit values and the tests' own services file only, ignoring
    .env and the shell's settings. The services file is read from the working directory, as
    ``.env`` is, so the tests run from the directory that holds theirs."""
    for name in list(os.environ):
        if name.startswith("CALL_") or name in ("ENVIRONMENT", "LANGSMITH_TRACING"):
            monkeypatch.delenv(name)
    for name, value in SERVICE_KEYS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(TESTS_DIR)

    def _make(**overrides: Any) -> CallConfig:
        values: dict[str, Any] = {"_env_file": None, **overrides}
        return CallConfig(**values)

    return _make


@pytest.fixture
def services_data() -> dict[str, Any]:
    """The tests' services file as raw data, fresh for each test so it can be mutated."""
    data: dict[str, Any] = yaml.safe_load((TESTS_DIR / "services.yaml").read_text())
    return data


@pytest.fixture
def use_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[[dict[str, Any]], None]:
    """Make the given data the services file the next CallConfig reads, in a working directory of
    its own."""

    def _use(data: dict[str, Any]) -> None:
        (tmp_path / "services.yaml").write_text(yaml.safe_dump(data))
        monkeypatch.chdir(tmp_path)

    return _use


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
