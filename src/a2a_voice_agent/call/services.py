from dataclasses import dataclass

from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openrouter.llm import OpenRouterLLMService
from pipecat.transcriptions.language import Language

from a2a_voice_agent.call.config import CallConfig
from a2a_voice_agent.contract import Brief

_LANGUAGE: dict[str, Language] = {"en": Language.EN, "cs": Language.CS}
_VOICES: dict[str, str] = {
    "cs": "82db1f84-5b96-4364-b04a-4c7ff80e2f8a",
    "en": "47c38ca4-5f35-497b-b1a3-415245fb35e1",
}


def language_for(brief: Brief) -> Language:
    """The language both speech services are pinned to for this call."""
    return _LANGUAGE[brief.language]


def voice_for(brief: Brief, config: CallConfig) -> str:
    """The voice for this call: the configured override, else the voice for the Brief's language."""
    return config.voice_id_override or _VOICES[brief.language]


@dataclass(frozen=True)
class Services:
    stt: DeepgramSTTService
    llm: OpenRouterLLMService
    tts: CartesiaTTSService


def _stt(brief: Brief, config: CallConfig) -> DeepgramSTTService:
    return DeepgramSTTService(
        api_key=config.deepgram_api_key.get_secret_value(),
        settings=DeepgramSTTService.Settings(
            model="nova-3",
            language=language_for(brief),
            interim_results=True,
            profanity_filter=True,
            smart_format=True,
        ),
    )


def _tts(brief: Brief, config: CallConfig) -> CartesiaTTSService:
    return CartesiaTTSService(
        api_key=config.cartesia_api_key.get_secret_value(),
        settings=CartesiaTTSService.Settings(
            voice=voice_for(brief, config),
            model=config.tts_model,
            language=language_for(brief),
        ),
    )


def _llm(config: CallConfig, system_instruction: str) -> OpenRouterLLMService:
    return OpenRouterLLMService(
        api_key=config.openrouter_api_key.get_secret_value(),
        settings=OpenRouterLLMService.Settings(
            model=config.llm_model,
            system_instruction=system_instruction,
        ),
    )


def build_services(brief: Brief, config: CallConfig, system_instruction: str) -> Services:
    return Services(
        stt=_stt(brief, config),
        llm=_llm(config, system_instruction),
        tts=_tts(brief, config),
    )
