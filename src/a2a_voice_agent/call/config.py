"""Deployment configuration for a call"""

from enum import StrEnum
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from a2a_voice_agent.call.services import ServicesConfig


class Environment(StrEnum):
    LOCAL = "local"
    PROD = "prod"
    EVAL = "eval"


class CallConfig(BaseSettings):
    """Behavioural settings for a call"""

    model_config = SettingsConfigDict(
        env_prefix="CALL_",
        env_file=".env",
        extra="ignore",
        frozen=True,
        validate_by_name=True,
    )

    environment: Environment = Field(default=Environment.PROD, validation_alias="ENVIRONMENT")
    tracing: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")

    # Which model and vendor fills each slot of the call, from services.yaml.
    services: ServicesConfig = Field(
        default_factory=lambda: ServicesConfig()  # type: ignore[call-arg]
    )

    timezone: ZoneInfo = ZoneInfo("Europe/Prague")  # Fixed to Prague for v1

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
