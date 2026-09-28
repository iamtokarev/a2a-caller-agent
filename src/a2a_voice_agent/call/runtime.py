"""Hold one call: build the pipeline, run it, return an Outcome."""

import asyncio
import uuid
from collections.abc import Sequence

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    CancelFrame,
    CancelWorkerFrame,
    EndFrame,
    EndWorkerFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    StartFrame,
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
from a2a_voice_agent.call.prompt import (
    PromptVariables,
    build_system_prompt,
    cap_warning,
    disclosure_text,
    stall_lines,
)
from a2a_voice_agent.call.services import Services, build_services
from a2a_voice_agent.call.session import CallSession
from a2a_voice_agent.call.stall import EscalationHandler, Stall
from a2a_voice_agent.call.tools import call_tools
from a2a_voice_agent.contract import Brief, Outcome


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


class _CallCap(FrameProcessor):
    """Tell the agent to wrap up shortly before the call cap, then cancel the call at the cap.

    Sits ahead of the user aggregator, so the warning is appended to the context and answered
    straight away. The warning is skipped once an Outcome has been reported; the cancellation
    happens whether or not the agent complied. The clock starts with the pipeline.
    """

    def __init__(self, session: CallSession, cap_secs: float, warning_lead_secs: float) -> None:
        super().__init__()
        self._session = session
        self._cap_secs = cap_secs
        self._warning_lead_secs = warning_lead_secs
        self._timer: asyncio.Task[None] | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._timer = self.create_task(self._run_timer())
        elif isinstance(frame, EndFrame | CancelFrame) and self._timer:
            await self.cancel_task(self._timer)
            self._timer = None

    async def _run_timer(self) -> None:
        await asyncio.sleep(self._cap_secs - self._warning_lead_secs)
        if self._session.outcome is None:
            logger.warning("Call cap in {}s; telling the agent to wrap up", self._warning_lead_secs)
            warning = cap_warning(self._warning_lead_secs)
            await self.push_frame(
                LLMMessagesAppendFrame([{"role": "system", "content": warning}], run_llm=True)
            )
        await asyncio.sleep(self._warning_lead_secs)
        logger.warning("Call cap of {}s reached; cancelling the call", self._cap_secs)
        self._session.cap_reached = True
        await self.push_frame(CancelWorkerFrame(reason="call cap reached"))


def _build_pipeline(
    transport: BaseTransport,
    services: Services,
    aggregators: LLMContextAggregatorPair,
    call_cap: FrameProcessor,
    disclosure: FrameProcessor,
    hang_up: FrameProcessor,
    stall: Stall,
    audio_buffer: AudioBufferProcessor | None,
) -> Pipeline:
    """Build the pipeline for a call."""
    recorder = [audio_buffer] if audio_buffer else []
    return Pipeline(
        [
            transport.input(),
            services.stt,
            call_cap,  # ahead of the user aggregator, which takes its warning into the context
            aggregators.user(),
            services.llm,
            disclosure,
            hang_up,
            stall,
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
    *,
    callee_opening: str | None = None,
    trace_tags: Sequence[str] = (),
) -> Outcome:
    """Hold one call for ``brief`` over ``transport`` and return what happened.

    A call that ends without the agent reporting an Outcome returns ``undetermined``. A call still
    running at ``config.call_cap_secs`` is cancelled.

    ``callee_opening`` is a line the Callee is taken to have said as the call connects, for a
    transport on which the Callee never speaks first. ``trace_tags`` label the call's trace.
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
    stall = Stall(escalate, config.stall_budget_secs, stall_lines(brief))
    context = LLMContext(tools=call_tools(stall.hold))
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
    call_cap = _CallCap(session, config.call_cap_secs, config.cap_warning_lead_secs)
    disclosure = _DisclosureOnFirstResponse(disclosure_text(brief))

    audio_buffer = trace_call(config, conversation_id)

    worker = PipelineWorker(
        _build_pipeline(
            transport,
            services,
            aggregators,
            call_cap,
            disclosure,
            _HangUpAfterGoodbye(session),
            stall,
            audio_buffer,
        ),
        name="callee_call",
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        app_resources=session,
        enable_tracing=audio_buffer is not None,
        conversation_id=conversation_id,
        additional_span_attributes=trace_attributes(config, trace_tags),
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

    if callee_opening:
        # Not on connect: until the transport's client says it is ready, it may not hear a reply.
        @worker.rtvi.event_handler("on_client_ready")
        async def _on_client_ready(rtvi: object) -> None:
            logger.info("Taking the Callee to have opened with {!r}", callee_opening)
            await worker.queue_frame(
                LLMMessagesAppendFrame([{"role": "user", "content": callee_opening}], run_llm=True)
            )

    await runner.add_workers(worker)
    await runner.run()

    return session.final_outcome()
