"""The call cap stays a backstop: nothing that runs inside a call may be as long as the cap."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from a2a_voice_agent.call.config import CallConfig


def test_default_timers_are_valid(make_config: Callable[..., CallConfig]) -> None:
    make_config()


def test_stall_budget_as_long_as_the_cap_is_rejected(
    make_config: Callable[..., CallConfig],
) -> None:
    with pytest.raises(ValidationError, match="stall_budget_secs"):
        make_config(stall_budget_secs=300, call_cap_secs=300)


def test_cap_warning_lead_as_long_as_the_cap_is_rejected(
    make_config: Callable[..., CallConfig],
) -> None:
    with pytest.raises(ValidationError, match="cap_warning_lead_secs"):
        make_config(cap_warning_lead_secs=300, call_cap_secs=300)
