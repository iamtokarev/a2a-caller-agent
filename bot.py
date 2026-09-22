import os
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import TransportParams

from a2a_voice_agent.call import run_call
from a2a_voice_agent.call.config import CallConfig
from a2a_voice_agent.contract import Brief, Escalation, EscalationAnswer
from a2a_voice_agent.utils import load_brief

load_dotenv(override=True)

DEFAULT_BRIEF = Path("briefs/dinner-en.json")
RUNS_DIR = Path("runs")


transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


async def escalate(escalation: Escalation) -> EscalationAnswer | None:
    logger.warning("Escalation raised with nobody to answer it: {}", escalation.question)
    return None


def _brief_path() -> Path:
    return Path(os.getenv("BRIEF_PATH", DEFAULT_BRIEF))


async def bot(runner_args: RunnerArguments) -> None:
    """The runner calls this once per connection: one connection, one call, one Outcome."""
    brief_path = _brief_path()
    brief: Brief = load_brief(brief_path)
    config = CallConfig()  # type: ignore[call-arg]

    transport = await create_transport(runner_args, transport_params)
    await run_call(brief, transport, config, escalate)

    # logger.info("Outcome: {}", outcome.model_dump_json())


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
