import asyncio
import os
import sys
import termios
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
from pipecat.evals.transport import EvalTransportParams
from pipecat.runner.types import EvalRunnerArguments, RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import TransportParams
from pydantic import BaseModel, ConfigDict

from a2a_voice_agent.call import run_call
from a2a_voice_agent.call.config import CallConfig, Environment
from a2a_voice_agent.contract import Brief, Escalation, EscalationAnswer
from a2a_voice_agent.utils import load_brief

load_dotenv(override=True)

DEFAULT_BRIEF = Path("briefs/dinner-en.json")
RUNS_DIR = Path("runs")

# Evals measure the shipped call timings, not the ones a local .env tunes for manual calls.
SHIPPED_TIMINGS = ("stall_budget_secs", "call_cap_secs", "cap_warning_lead_secs")


transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
    "eval": lambda: EvalTransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
    ),
}


class EvalRun(BaseModel):
    """What an eval suite tells the bot about one Scenario, through the runner body."""

    model_config = ConfigDict(extra="forbid")

    scenario: str | None = None
    brief: Path | None = None
    # A Simulated Callee only ever answers, so the Callee is taken to have opened with this.
    callee_opening: str | None = None
    # The Principal's answer to any Escalation; none means the Principal never answers.
    principal_answer: str | None = None

    async def escalate(self, escalation: Escalation) -> EscalationAnswer | None:
        """Answer as the scripted Principal would, at once."""
        logger.info("Escalation answered by the scripted Principal: {}", escalation.question)
        return EscalationAnswer(text=self.principal_answer) if self.principal_answer else None


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


def _eval_config() -> CallConfig:
    """The call's settings from the environment, with the eval environment and the shipped
    timings pinned: explicit values win over whatever the environment says."""
    shipped = {name: CallConfig.model_fields[name].default for name in SHIPPED_TIMINGS}
    return CallConfig(environment=Environment.EVAL, **shipped)


def _brief_path() -> Path:
    return Path(os.getenv("BRIEF_PATH", DEFAULT_BRIEF))


async def bot(runner_args: RunnerArguments) -> None:
    """The runner calls this once per connection: one connection, one call, one Outcome."""
    transport = await create_transport(runner_args, transport_params)

    if isinstance(runner_args, EvalRunnerArguments):
        run = EvalRun.model_validate(runner_args.body or {})
        brief: Brief = load_brief(run.brief or _brief_path())
        outcome = await run_call(
            brief,
            transport,
            _eval_config(),
            run.escalate,
            callee_opening=run.callee_opening,
            trace_tags=[run.scenario] if run.scenario else [],
        )
    else:
        brief = load_brief(_brief_path())
        config = CallConfig()  # type: ignore[call-arg]
        outcome = await run_call(brief, transport, config, escalate)

    logger.info("Outcome: {}", outcome.model_dump_json())


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
