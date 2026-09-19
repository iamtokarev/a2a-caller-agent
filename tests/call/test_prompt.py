"""The system prompt carries every Constraint the agent may not break.

The prompt's wording is deliberately not asserted: it is the experimental surface of this work.
"""

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from a2a_voice_agent.call.config import CallConfig
from a2a_voice_agent.call.prompt import PromptVariables, build_system_prompt
from a2a_voice_agent.contract import Brief

NOW = PromptVariables(now=datetime(2026, 9, 19, 12, 0, tzinfo=ZoneInfo("Europe/Prague")))


def test_every_non_negotiable_constraint_appears_in_the_prompt(
    make_brief: Callable[..., Brief], make_config: Callable[..., CallConfig]
) -> None:
    brief = make_brief(
        constraints=[
            {"description": "The booking must be for this Saturday.", "negotiable": False},
            {"description": "The table must seat four people.", "negotiable": False},
            {"description": "Indoor seating is preferred.", "negotiable": True},
        ]
    )

    prompt = build_system_prompt(brief, make_config(), NOW)

    assert "The booking must be for this Saturday." in prompt
    assert "The table must seat four people." in prompt
