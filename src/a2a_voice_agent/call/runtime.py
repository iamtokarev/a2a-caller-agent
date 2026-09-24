"""Hold one call: build the pipeline, run it, return an Outcome."""

import uuid
from collections.abc import Awaitable, Callable

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    EndWorkerFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_transport import BaseTransport
from pipecat.turns.user_mute import FirstSpeechUserMuteStrategy
from pipecat.workers.runner import WorkerRunner

from a2a_voice_agent.call.config import CallConfig
from a2a_voice_agent.call.observability import trace_attributes, trace_call
from a2a_voice_agent.call.prompt import PromptVariables, build_system_prompt, disclosure_text
from a2a_voice_agent.call.services import Services, build_services
from a2a_voice_agent.call.session import CallSession
from a2a_voice_agent.call.tools import TOOLS
from a2a_voice_agent.contract import (
    Brief,
    Escalation,
    EscalationAnswer,
    Outcome,
)

# How the conversation layer asks the Principal a question it has no authority to answer.
EscalationHandler = Callable[[Escalation], Awaitable[EscalationAnswer | None]]


class _DisclosureOnFirstResponse(FrameProcessor):
    """Open the agent's first response with the Disclosure, spoken before the LLM's own words.

    Sits between the LLM and TTS. The LLM only responds once the Callee's turn has ended, so the
    Disclosure never talks over the Callee, and it reaches TTS and the context as part of the
    same response it opens.

    The Disclosure counts as spoken once the bot starts talking. A response interrupted before
    that is dropped with its Disclosure, so the next response opens with it again.
    """

    def __init__(self, disclosure: str) -> None:
        super().__init__()
        self._disclosure = disclosure
        self._pending = False
        self._spoken = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        if self._spoken:
            return
        if isinstance(frame, LLMFullResponseStartFrame) and not self._pending:
            self._pending = True
            logger.info("Speaking the Disclosure")
            await self.push_frame(LLMTextFrame(f"{self._disclosure} "), direction)
        elif isinstance(frame, BotStartedSpeakingFrame) and self._pending:
            self._spoken = True
        elif isinstance(frame, InterruptionFrame) and self._pending:
            logger.info("Disclosure interrupted before it was spoken; retrying on next response")
            self._pending = False


class _HangUpAfterGoodbye(FrameProcessor):
    """End the call once the response that follows the Outcome has been spoken.

    Sits between the LLM and TTS. Recording the Outcome prompts one more response, the goodbye.
    An ``EndWorkerFrame`` pushed right behind it drains that speech through TTS before the worker
    stops, so the goodbye is heard in full. A goodbye the Callee interrupts does not hang up; the
    next response does.
    """

    def __init__(self, session: CallSession) -> None:
        super().__init__()
        self._session = session
        self._closing = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            # The response carrying report_outcome started before the Outcome was recorded.
            self._closing = self._session.outcome is not None
        elif isinstance(frame, InterruptionFrame):
            self._closing = False
        elif isinstance(frame, LLMFullResponseEndFrame) and self._closing:
            self._closing = False
            logger.info("Hanging up after the goodbye")
            await self.push_frame(EndWorkerFrame(), FrameDirection.DOWNSTREAM)


def _build_pipeline(
    transport: BaseTransport,
    services: Services,
    aggregators: LLMContextAggregatorPair,
    disclosure: FrameProcessor,
    hang_up: FrameProcessor,
    audio_buffer: AudioBufferProcessor | None,
) -> Pipeline:
    """Build the pipeline for a call."""
    recorder = [audio_buffer] if audio_buffer else []
    return Pipeline(
        [
            transport.input(),
            services.stt,
            aggregators.user(),
            services.llm,
            disclosure,
            hang_up,
            services.tts,
            transport.output(),
            *recorder,
            aggregators.assistant(),
        ]
    )


async def run_call(
    brief: Brief,
    transport: BaseTransport,
    config: CallConfig,
    escalate: EscalationHandler,
) -> Outcome:
    """Hold one call for ``brief`` over ``transport`` and return what happened.

    A call that ends without the agent reporting an Outcome returns ``undetermined``.
    """
    conversation_id = str(uuid.uuid4())
    logger.info(
        "Starting call {} to {} for {}",
        conversation_id,
        brief.callee.name,
        brief.principal.name,
    )

    variables = PromptVariables.at(config.timezone)
    system_prompt = build_system_prompt(brief, config, variables)
    services = build_services(brief, config, system_prompt)

    session = CallSession()
    context = LLMContext(tools=TOOLS)
    aggregators = LLMContextAggregatorPair(
        context,
        # Turn-taking keeps the framework's default stop strategy, Smart Turn v3, which does not
        # cover Czech. A candidate to replace with a plain speech-timeout strategy for both
        # languages if Czech turn-taking proves unreliable.
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            # The Callee cannot barge in on the Disclosure.
            user_mute_strategies=[FirstSpeechUserMuteStrategy()],
        ),
    )
    disclosure = _DisclosureOnFirstResponse(disclosure_text(brief))

    audio_buffer = trace_call(config, conversation_id)

    worker = PipelineWorker(
        _build_pipeline(
            transport, services, aggregators, disclosure, _HangUpAfterGoodbye(session), audio_buffer
        ),
        name="callee_call",
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        app_resources=session,
        enable_tracing=audio_buffer is not None,
        conversation_id=conversation_id,
        additional_span_attributes=trace_attributes(config),
    )
    runner = WorkerRunner(handle_sigint=False)

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(transport: BaseTransport, client: object) -> None:
        logger.info("Callee disconnected")
        await runner.cancel()

    @transport.event_handler("on_client_connected")
    async def _on_connected(transport: BaseTransport, client: object) -> None:
        session.answered = True
        if audio_buffer:
            await audio_buffer.start_recording()

    await runner.add_workers(worker)
    await runner.run()

    return session.final_outcome()
