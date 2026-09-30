"""A call's services are the ones the services file names, configured as it says; the Brief's
language pins both speech services and picks the voice."""

import importlib
from collections.abc import Callable
from typing import Any, cast, get_args

import pytest
from pipecat.services.ai_service import AIService
from pipecat.services.settings import LLMSettings
from pipecat.transcriptions.language import Language

from a2a_voice_agent.call.config import CallConfig
from a2a_voice_agent.call.services import Services
from a2a_voice_agent.contract import Brief
from a2a_voice_agent.contract import Language as BriefLanguage


@pytest.fixture
def services_for(
    make_brief: Callable[..., Brief], make_config: Callable[..., CallConfig]
) -> Callable[..., Services]:
    """Build a call's services from the services file in use, for a Brief in the given language."""

    def build(language: str = "en") -> Services:
        return make_config().services.build(make_brief(language=language), "system prompt")

    return build


def _named_class(path: str) -> type:
    module, _, name = path.rpartition(".")
    cls: type = getattr(importlib.import_module(module), name)
    return cls


@pytest.mark.parametrize("slot", ["stt", "tts"])
def test_each_speech_slot_is_the_service_the_file_names_with_its_settings(
    services_for: Callable[..., Services], services_data: dict[str, Any], slot: str
) -> None:
    service: AIService = getattr(services_for(), slot)

    assert isinstance(service, _named_class(services_data[slot]["service"]))
    for name, value in services_data[slot].get("settings", {}).items():
        assert getattr(service._settings, name) == value


def test_llm_runs_the_files_model_with_the_system_prompt(
    services_for: Callable[..., Services], services_data: dict[str, Any]
) -> None:
    settings = cast(LLMSettings, services_for().llm._settings)

    assert settings.model == services_data["llm"]["model"]
    assert settings.system_instruction == "system prompt"


@pytest.mark.parametrize(("language", "expected"), [("cs", Language.CS), ("en", Language.EN)])
def test_speech_services_are_pinned_to_the_brief_language(
    services_for: Callable[..., Services], language: str, expected: Language
) -> None:
    services = services_for(language)

    assert services.stt._settings.language == expected
    assert services.tts._settings.language == expected


@pytest.mark.parametrize("language", get_args(BriefLanguage))
def test_each_brief_language_speaks_in_the_files_voice_for_it(
    services_for: Callable[..., Services], services_data: dict[str, Any], language: str
) -> None:
    assert services_for(language).tts._settings.voice == services_data["tts"]["voices"][language]


def test_api_keys_are_read_once_when_the_config_loads(
    make_brief: Callable[..., Brief],
    make_config: Callable[..., CallConfig],
    services_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config()
    for slot in services_data.values():
        monkeypatch.delenv(slot["api_key_env"])

    services = config.services.build(make_brief(), "system prompt")

    assert services.llm is not None
    assert services.stt is not None
    assert services.tts is not None
