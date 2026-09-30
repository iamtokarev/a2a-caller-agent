"""The services a call runs on: which model and vendor fills each slot, read from services.yaml, and
building them for a Brief (ADR 0005)."""

import os
from dataclasses import dataclass
from typing import Any, Self, get_args

from pipecat.services.ai_service import AIService
from pipecat.services.openrouter.llm import OpenRouterLLMService
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language as SpeechLanguage
from pydantic import BaseModel, ConfigDict, ImportString, PrivateAttr, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from a2a_voice_agent.contract import Brief, Language

_SPEECH_LANGUAGE: dict[Language, SpeechLanguage] = {
    "en": SpeechLanguage.EN,
    "cs": SpeechLanguage.CS,
}


@dataclass(frozen=True)
class Services:
    stt: STTService
    llm: OpenRouterLLMService
    tts: TTSService


class _Spec(BaseModel):
    """One slot of the call. Its API key is read once, as the file loads, from the environment
    variable ``api_key_env``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key_env: str

    _api_key: SecretStr = PrivateAttr()

    @model_validator(mode="after")
    def _read_api_key(self) -> Self:
        if self.api_key_env not in os.environ:
            raise ValueError(f"{self.api_key_env} is not set in the environment")
        self._api_key = SecretStr(os.environ[self.api_key_env])
        return self


class LLMSpec(_Spec):
    """The LLM slot: always OpenRouter, so only the model is chosen."""

    model: str

    def build(self, system_instruction: str) -> OpenRouterLLMService:
        return OpenRouterLLMService(
            api_key=self._api_key.get_secret_value(),
            settings=OpenRouterLLMService.Settings(
                model=self.model,
                system_instruction=system_instruction,
            ),
        )


class _SpeechSpec[S: AIService](_Spec):
    """A speech slot: the Pipecat service that fills it, and the settings applied over the
    service's defaults. The Brief's language is applied over those."""

    service: ImportString[type[S]]
    settings: dict[str, Any] = {}

    @model_validator(mode="after")
    def _settings_fit_the_service(self) -> Self:
        # Pipecat keeps keys its Settings do not declare rather than rejecting them, so a typo
        # would be accepted and do nothing.
        settings = self.service.Settings.from_mapping(self.settings)  # type: ignore[attr-defined]
        if unknown := settings.extra:
            raise ValueError(
                f"{self.service.__name__}.Settings has no field {', '.join(sorted(unknown))}"
            )
        return self

    def _build(self, language: Language, **per_call: Any) -> S:
        settings_class = self.service.Settings  # type: ignore[attr-defined]
        settings = settings_class.from_mapping(
            {**self.settings, "language": _SPEECH_LANGUAGE[language], **per_call}
        )
        return self.service(api_key=self._api_key.get_secret_value(), settings=settings)


class STTSpec(_SpeechSpec[STTService]):
    """The speech-to-text slot."""

    def build(self, language: Language) -> STTService:
        return self._build(language)


class TTSSpec(_SpeechSpec[TTSService]):
    """The text-to-speech slot, with one voice for each Brief language."""

    voices: dict[Language, str]

    @model_validator(mode="after")
    def _voice_for_every_language(self) -> Self:
        if missing := sorted(set(get_args(Language)) - self.voices.keys()):
            raise ValueError(f"no voice for Brief language {', '.join(missing)}")
        return self

    def build(self, language: Language) -> TTSService:
        return self._build(language, voice=self.voices[language])


class ServicesConfig(BaseSettings):
    """Which model and vendor fills each slot of a call: the contents of services.yaml, read from
    the working directory like .env."""

    model_config = SettingsConfigDict(yaml_file="services.yaml", extra="forbid", frozen=True)

    llm: LLMSpec
    stt: STTSpec
    tts: TTSSpec

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The file alone chooses the models; only the API keys come from the environment.
        return (init_settings, YamlConfigSettingsSource(settings_cls))

    def build(self, brief: Brief, system_instruction: str) -> Services:
        """The services for a call on ``brief``, pinned to its language."""
        return Services(
            stt=self.stt.build(brief.language),
            llm=self.llm.build(system_instruction),
            tts=self.tts.build(brief.language),
        )
