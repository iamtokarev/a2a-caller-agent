"""The Brief's language decides the speech language and the voice; the override wins."""

from collections.abc import Callable

import pytest
from pipecat.transcriptions.language import Language

from a2a_voice_agent.call.config import CallConfig
from a2a_voice_agent.call.services import build_services, language_for, voice_for
from a2a_voice_agent.contract import Brief


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
