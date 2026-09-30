"""The call cap stays a backstop: nothing that runs inside a call may be as long as the cap.
A services file that cannot build the call's services stops the bot before any call."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

import pytest
import yaml
from pydantic import ValidationError

from a2a_voice_agent.call.config import CallConfig
from a2a_voice_agent.contract import Brief
from a2a_voice_agent.contract import Language as BriefLanguage

# The services file the bot ships with, whatever vendors it currently names.
SHIPPED_SERVICES = Path(__file__).resolve().parents[2] / "services.yaml"


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


def test_shipped_services_file_builds_the_services_for_every_brief_language(
    make_config: Callable[..., CallConfig],
    make_brief: Callable[..., Brief],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for slot in yaml.safe_load(SHIPPED_SERVICES.read_text()).values():
        monkeypatch.setenv(slot["api_key_env"], "test-key")
    monkeypatch.chdir(SHIPPED_SERVICES.parent)

    services = make_config().services

    for language in get_args(BriefLanguage):
        services.build(make_brief(language=language), "system prompt")


def _set(slot: str, key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def change(services: dict[str, Any]) -> None:
        services[slot][key] = value

    return change


def _drop_czech_voice(services: dict[str, Any]) -> None:
    del services["tts"]["voices"]["cs"]


def _add_voice_override(services: dict[str, Any]) -> None:
    services["voice_override"] = "someone"


@pytest.mark.parametrize(
    ("break_services", "error"),
    [
        (
            _set("stt", "service", "pipecat.services.nowhere.stt.NowhereSTTService"),
            "Invalid python path",
        ),
        (
            _set("stt", "service", "pipecat.services.cartesia.tts.CartesiaTTSService"),
            "subclass of STTService",
        ),
        (
            _set("tts", "service", "pipecat.services.deepgram.stt.DeepgramSTTService"),
            "subclass of TTSService",
        ),
        (_set("stt", "settings", {"modle": "any-model"}), "has no field modle"),
        (_set("llm", "api_key_env", "NOWHERE_API_KEY"), "NOWHERE_API_KEY is not set"),
        (_set("stt", "api_key_env", "NOWHERE_API_KEY"), "NOWHERE_API_KEY is not set"),
        (_drop_czech_voice, "no voice for Brief language cs"),
        (_add_voice_override, "voice_override\n  Extra inputs are not permitted"),
    ],
)
def test_services_file_that_cannot_build_the_services_is_rejected(
    make_config: Callable[..., CallConfig],
    services_data: dict[str, Any],
    use_services: Callable[[dict[str, Any]], None],
    break_services: Callable[[dict[str, Any]], None],
    error: str,
) -> None:
    break_services(services_data)
    use_services(services_data)

    with pytest.raises(ValidationError, match=error):
        make_config()


def test_missing_services_file_is_rejected(
    make_config: Callable[..., CallConfig], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError, match="llm\n  Field required"):
        make_config()
