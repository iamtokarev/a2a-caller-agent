"""Deployment configuration for a call"""

from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DisclosureStyle(StrEnum):
    """Which Disclosure phrasing the prompt asks for. The phrasings live in the prompt."""

    PLAIN = "plain"
    WARM = "warm"
    BRIEF = "brief"


class CallConfig(BaseSettings):
    """Behavioural settings for a call"""

    model_config = SettingsConfigDict(
        env_prefix="CALL_",
        env_file=".env",
        extra="ignore",
        frozen=True,
    )

    llm_model: str = "openai/gpt-5.6-luna"
    tts_vendor: Literal["cartesia", "elevenlabs"] = "elevenlabs"
    voice_id_override: str | None = None
    disclosure_style: DisclosureStyle = DisclosureStyle.PLAIN

    stall_budget_secs: float = Field(default=60.0, gt=0)
    call_cap_secs: float = Field(default=300.0, gt=0)
    cap_warning_lead_secs: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def _timers_fit_inside_cap(self) -> Self:
        # The cap is a backstop (ADR 0002): a stall that can reach it makes it the mechanism.
        if self.stall_budget_secs >= self.call_cap_secs:
            raise ValueError("stall_budget_secs must be shorter than call_cap_secs")
        if self.cap_warning_lead_secs >= self.call_cap_secs:
            raise ValueError("cap_warning_lead_secs must be shorter than call_cap_secs")
        return self
