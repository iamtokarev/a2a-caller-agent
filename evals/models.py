"""The model that plays the Simulated Callee and judges every Scenario.

Scenario files name ``openrouter`` as the ``factory:`` of both.
"""

import os

from pipecat.services.openrouter.llm import OpenRouterLLMService

# One model plays the Callee and judges, from a different family than the agent's.
MODEL = "z-ai/glm-5.3-flashx"


def openrouter(config: dict[str, object]) -> OpenRouterLLMService:
    """``MODEL`` on OpenRouter, for the judge and the Simulated Callee alike."""
    return OpenRouterLLMService(
        api_key=os.environ["OPENROUTER_API_KEY"],
        settings=OpenRouterLLMService.Settings(model=MODEL),
    )
