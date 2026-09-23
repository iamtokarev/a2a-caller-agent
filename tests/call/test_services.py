"""The Brief's language decides the speech language and the voice; the override wins."""

from collections.abc import Callable
from typing import cast, get_args

import pytest
from pipecat.services.settings import STTSettings, TTSSettings
from pipecat.transcriptions.language import Language

from a2a_voice_agent.call.config import CallConfig
from a2a_voice_agent.call.services import build_services, language_for, voice_for
from a2a_voice_agent.contract import Brief
from a2a_voice_agent.contract import Language as BriefLanguage


@pytest.mark.parametrize(("language", "expected"), [("cs", Language.CS), ("en", Language.EN)])
def test_brief_language_pins_the_speech_language(
    make_brief: Callable[..., Brief], language: str, expected: Language
) -> None:
    assert language_for(make_brief(language=language)) is expected


def test_czech_and_english_briefs_get_different_voices(
    make_brief: Callable[..., Brief], make_config: Callable[..., CallConfig]
) -> None:
    config = make_config()

    assert voice_for(make_brief(language="cs"), config) != voice_for(
        make_brief(language="en"), config
    )


@pytest.mark.parametrize("language", ["cs", "en"])
def test_voice_override_wins_in_every_language(
    make_brief: Callable[..., Brief], make_config: Callable[..., CallConfig], language: str
) -> None:
    config = make_config(voice_id_override="override-voice")

    assert voice_for(make_brief(language=language), config) == "override-voice"


def test_services_build_for_a_valid_brief(
    make_brief: Callable[..., Brief], make_config: Callable[..., CallConfig]
) -> None:
    services = build_services(make_brief(), make_config(), "system prompt")

    assert services.stt is not None
    assert services.llm is not None
    assert services.tts is not None


@pytest.mark.parametrize(("language", "expected"), [("cs", Language.CS), ("en", Language.EN)])
def test_speech_to_text_is_pinned_to_the_brief_language(
    make_brief: Callable[..., Brief],
    make_config: Callable[..., CallConfig],
    language: str,
    expected: Language,
) -> None:
    services = build_services(make_brief(language=language), make_config(), "system prompt")

    assert cast(STTSettings, services.stt._settings).language == expected


@pytest.mark.parametrize("language", ["cs", "en"])
def test_text_to_speech_gets_the_brief_language_and_its_voice(
    make_brief: Callable[..., Brief], make_config: Callable[..., CallConfig], language: str
) -> None:
    brief = make_brief(language=language)
    config = make_config()

    settings = cast(TTSSettings, build_services(brief, config, "system prompt").tts._settings)

    assert settings.language == language
    assert settings.voice == voice_for(brief, config)


def test_voice_override_is_read_from_the_environment(
    make_brief: Callable[..., Brief],
    make_config: Callable[..., CallConfig],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CALL_VOICE_ID_OVERRIDE", "voice-from-env")

    assert voice_for(make_brief(language="cs"), make_config()) == "voice-from-env"


def test_every_supported_language_has_a_speech_language_and_a_voice(
    make_brief: Callable[..., Brief], make_config: Callable[..., CallConfig]
) -> None:
    for language in get_args(BriefLanguage):
        brief = make_brief(language=language)
        assert language_for(brief)
        assert voice_for(brief, make_config())
