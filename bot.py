import asyncio
import os
import sys
import termios
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


async def _read_line() -> str:
    """Read one line from the terminal without blocking the call."""
    loop = asyncio.get_running_loop()
    line: asyncio.Future[str] = loop.create_future()

    def on_ready() -> None:
        if not line.done():
            line.set_result(sys.stdin.readline())

    loop.add_reader(sys.stdin.fileno(), on_ready)
    try:
        return await line
    finally:
        loop.remove_reader(sys.stdin.fileno())


async def escalate(escalation: Escalation) -> EscalationAnswer | None:
    """Ask the Principal in this terminal: a number picks an option, an empty line is no answer."""
    options = escalation.options or []
    if sys.stdin.isatty():
        # Whatever was typed before the question is not an answer to it.
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    print(f"\nESCALATION: {escalation.question}")
    for number, option in enumerate(options, start=1):
        print(f"  {number}. {option}")
    print("Answer: ", end="", flush=True)

    text = (await _read_line()).strip()
    if not text:
        return None
    if text.isdigit() and 1 <= int(text) <= len(options):
        index = int(text) - 1
        return EscalationAnswer(text=options[index], chosen_option_index=index)
    return EscalationAnswer(text=text)


def _brief_path() -> Path:
    return Path(os.getenv("BRIEF_PATH", DEFAULT_BRIEF))


async def bot(runner_args: RunnerArguments) -> None:
    """The runner calls this once per connection: one connection, one call, one Outcome."""
    brief_path = _brief_path()
    brief: Brief = load_brief(brief_path)
    config = CallConfig()  # type: ignore[call-arg]

    transport = await create_transport(runner_args, transport_params)
    outcome = await run_call(brief, transport, config, escalate)

    logger.info("Outcome: {}", outcome.model_dump_json())


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
