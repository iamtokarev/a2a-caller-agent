# Pipecat patterns for goal-driven phone calls

Research for [`.scratch/v1-end-to-end/issues/04-pipecat-patterns-for-phone-calls.md`](../../.scratch/v1-end-to-end/issues/04-pipecat-patterns-for-phone-calls.md).

**Source**: the `pipecat-context-hub` MCP index, refreshed 2026-09-05, `indexed_framework_version: 1.8.1`,
`indexed_framework_commits_ahead: 0` — i.e. exactly the version pinned in `pyproject.toml`
(`pipecat-ai[...]>=1.8.1`). Framework source cited at commit `5ff3201`; examples at commit `87ede77`
(`pipecat-ai/pipecat-examples`). No claim below comes from `.venv` or from training data.

**Vocabulary**: this doc uses `CONTEXT.md` terms — Client, Principal, Callee, Brief, Objective,
Outcome, Escalation, Disclosure. Pipecat's own docs say "user" for the party on the far end of the
line; in our terms that is the **Callee**.

**Headline**: substantially more of this is already in the framework than
[`docs/initial-research.md`](../initial-research.md) assumes. Voicemail detection, IVR navigation,
DTMF in both directions, graceful hang-up, LLM/TTS failover and websocket reconnection are all
first-class in 1.8.1. Three things in that older doc are now **wrong** — see
[Corrections to `initial-research.md`](#corrections-to-initial-researchmd). What we genuinely have to
build is small and specific — see [What we must build](#what-we-must-build).

---

## 1. Function calling and tool use

**Framework provides.** The current API is *direct functions*: one `async` function that is both
handler and schema. Pipecat derives name, description, parameter properties and required-ness from
the signature and a Google-style docstring. First parameter is always `params: FunctionCallParams`.
Register by listing the function itself in `LLMContext(tools=[...])`.
([`/pipecat/learn/function-calling.md`](https://docs.pipecat.ai/pipecat/learn/function-calling.md),
§"1. Define a tool", §"2. Add the tool to the context")

Knobs that matter for a call:

| Knob | Where | Effect |
| --- | --- | --- |
| `@tool_options(cancel_on_interruption=False)` | `pipecat.adapters.schemas.direct_function` | Makes the call **asynchronous**: the LLM carries on talking while the tool runs; the result is injected later as a developer message and triggers a fresh inference |
| `@tool_options(timeout_secs=N)` | same | Per-tool deadline. On expiry the handler gets `asyncio.CancelledError`, the call settles as cancelled, and inference runs so the bot can say it did not complete |
| `@tool_options(cancellable_by_llm=True)` | same | Pipecat advertises a matching `cancel_<name>` tool so the model can abandon a long call. Only meaningful with `cancel_on_interruption=False` |
| `FunctionCallResultProperties(is_final=False)` | `pipecat.frames.frames` | Intermediate progress updates from an async tool, before the final result |
| `FunctionCallResultProperties(run_llm=False)` | same | Return a result without triggering a completion |
| `LLMSetToolsFrame(tools=[...])` | `pipecat.frames.frames` | Swap the whole advertised tool set mid-call |

(all from [`/pipecat/learn/function-calling.md`](https://docs.pipecat.ai/pipecat/learn/function-calling.md))

`FunctionCallParams` carries `function_name`, `tool_call_id`, `arguments`, `llm`, `pipeline_worker`,
`context`, `result_callback`, `app_resources`, `worker_runner`. Note `app_resources` is the
documented seam for shared state: it is passed to `PipelineWorker(app_resources=...)`, **passed by
reference**, and "the caller retains their handle and can read mutations after the task finishes"
(ibid., §"Sharing Resources with app_resources"). `params.tool_resources` /
`PipelineWorker(tool_resources=...)` are deprecated aliases.

**How a tool emits a structured Outcome.** There is no Pipecat concept of a "call result". The
documented mechanism is the one above: a `report_outcome`-style direct function writes the structured
Outcome onto the `app_resources` dataclass the A2A executor also holds, then resolves
`params.result_callback(...)`. The framework guarantees the reference is never copied or cleared, so
the A2A side reads it after `on_pipeline_finished`. Everything about the *shape* of that Outcome is
ours (settled in [ADR 0001](../adr/0001-brief-and-outcome-contract.md)).

**Watch-out.** `run_in_parallel` and `group_parallel_tools` both default to `True` on `LLMService`.
With `group_parallel_tools=False`, each tool result re-triggers the LLM, so the bot answers once per
tool instead of once total (ibid., §"Parallel and Multiple Tool Calls").

---

## 2. Ending the call from inside the pipeline

**Framework provides.** Four termination frames, with a documented table
([`/pipecat/learn/pipeline-termination.md`](https://docs.pipecat.ai/pipecat/learn/pipeline-termination.md),
§"Termination frames at a glance"):

| Frame | Job | Pushed from |
| --- | --- | --- |
| `EndFrame` | Graceful; drains pending frames | outside, via `worker.queue_frame(EndFrame())` |
| `CancelFrame` | Immediate; discards pending frames | `worker.cancel()` |
| `EndWorkerFrame` | Graceful signal from **inside** the pipeline | `push_frame(EndWorkerFrame(), FrameDirection.DOWNSTREAM)` — the source converts it to `EndFrame` |
| `CancelWorkerFrame` | Immediate signal from inside | likewise → `CancelFrame` |

The graceful hang-up path from a tool is exactly:

```python
async def end_conversation(params: FunctionCallParams):
    """End the conversation and shut down the bot."""
    await params.llm.push_frame(TTSSpeakFrame("Have a nice day!"))
    await params.result_callback({"status": "ended"})  # MUST come before the end frame
    await params.llm.push_frame(EndWorkerFrame(), FrameDirection.DOWNSTREAM)
```

The docs warn explicitly: **always call `result_callback` before pushing the end frame**, or the LLM
function call is left unresolved (pass `None` if you don't want a reply). The `ivr-navigation`
example does the same thing in five lines
([`ivr-navigation/bot.py`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede77/ivr-navigation/bot.py)).

**Ending the call *leg*, not just the pipeline.** For WebSocket telephony this is the serializer's
job, and it is on by default. `TwilioFrameSerializer` has `auto_hang_up=True`; on an `EndFrame` or
`CancelFrame` it calls Twilio's hangup API. It requires `call_sid`, `account_sid` and `auth_token`
and raises `ValueError` at construction if any are missing
([`/pipecat/telephony/twilio-websockets.md`](https://docs.pipecat.ai/pipecat/telephony/twilio-websockets.md),
§"Automatic Call Termination"). Telnyx and Plivo have the same `auto_hang_up` default and the same
credential requirement; **Exotel and Vonage have no auto hang-up at all**
([serializer notes for
exotel](https://docs.pipecat.ai/api-reference/server/services/serializers/exotel.md),
[plivo](https://docs.pipecat.ai/api-reference/server/services/serializers/plivo.md),
[telnyx](https://docs.pipecat.ai/api-reference/server/services/serializers/telnyx.md),
[vonage](https://docs.pipecat.ai/api-reference/server/services/serializers/vonage.md)).

**Other termination paths, all built in:**

- **Idle detection is on by default** — `PipelineWorker(cancel_on_idle_timeout=True,
  idle_timeout_secs=300, idle_timeout_frames=(BotSpeakingFrame, UserSpeakingFrame))`. This is a
  pipeline-level safety net, distinct from the per-turn idle detection in §6.
- **Maximum call duration is not built in.** The documented pattern is an `asyncio` timer that
  speaks a goodbye then queues an `EndFrame`.
- **`on_pipeline_finished`** fires after *any* terminal state — graceful or cancelled — and the docs
  name it "the single write point for end-of-call work like saving a transcript". `on_client_disconnected`
  fires only on disconnect and should be used to *tag the reason*, not to persist. This is the right
  hook for emitting the Outcome exactly once.

**Watch-out.** A custom `FrameProcessor` that does not `push_frame()` everything downstream will
block termination frames and produce a `dangling tasks detected` warning on shutdown (ibid.,
§"Troubleshooting").

---

## 3. Turn-taking, interruption, barge-in

This is the area that changed most since `initial-research.md` was written, and it is where the
tuning knobs for a phone call live.

**The 1.0 migration moved VAD and turn config off the transport and onto the user aggregator**, and
changed the `VADParams` default `stop_secs` from 0.8 to 0.2
([`/pipecat/migration/migration-1.0.md`](https://docs.pipecat.ai/pipecat/migration/migration-1.0.md),
§"3. VAD & Turn Analyzer Configuration"). Our `pipecat-bot/bot.py` already does this correctly
(`LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())`).

### The model

Turn detection is two independent lists of *strategies* on
`LLMUserAggregatorParams(user_turn_strategies=UserTurnStrategies(start=[...], stop=[...]))`
([`/api-reference/server/utilities/turn-management/user-turn-strategies.md`](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies.md)).

**Defaults**: start = `[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()]`;
stop = `[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]`.

**Start strategies** (any one triggering opens the turn, and by default broadcasts an interruption):

| Strategy | Use |
| --- | --- |
| `VADUserTurnStartStrategy` | Most responsive; fires on VAD |
| `TranscriptionUserTurnStartStrategy(use_interim=…)` | Fallback when VAD misses soft speech |
| `MinWordsUserTurnStartStrategy(min_words=N)` | **Barge-in filter**: requires N words before interrupting. Docs note the threshold applies *only while the bot is speaking* — 1 word is enough otherwise |
| `WakePhraseUserTurnStartStrategy` | Not relevant to outbound |
| `KrispVivaIPUserTurnStartStrategy(threshold=…)` | Model-based backchannel filter ("uh-huh" vs a real interruption). Needs the Krisp SDK and a `.kef` model |
| `ExternalUserTurnStartStrategy` | Defers to a turn-detecting STT service |

**Stop strategies** — the doc is explicit that **this is where you tune responsiveness, not
`stop_secs`**: "The VAD waits a short, fixed interval (default `0.2s`) … This is a low-level
detection threshold — leave it alone. To change wait time, use the stop strategy."

| Strategy | Decides the turn is over when… |
| --- | --- |
| `SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)` | A fixed silence window elapses |
| `TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())` | The Smart Turn v3 model predicts end-of-turn (the default) |
| `FilterIncompleteUserTurnStrategies()` | An LLM judges the utterance semantically complete (marker-based, `●`) |
| `ExternalUserTurnStopStrategy` / `ExternalUserTurnCompletionStopStrategy` | An external component decides |

**Where latency actually comes from** (ibid., §"Where the latency comes from"): the strategies do
*not* stack. Each stop strategy waits for a transcript (`wait_for_transcript`, default `True`), and
uses the STT provider's reported `ttfs_p99_latency` as an absolute fallback deadline, short-circuited
the moment a finalised transcript arrives. `VADParams.stop_secs` and turn-model inference both fall
*inside* that budget. Built-in STT P99 values assume `stop_secs=0.2`; change it and the strategy logs
a warning telling you to re-run [stt-benchmark](https://github.com/pipecat-ai/stt-benchmark) and pass
your measured value as `ttfs_p99_latency`.

`user_turn_stop_timeout` (default `5.0s`, on `LLMUserAggregatorParams`) is a watchdog, not part of
normal timing.

### Interruption mechanics

`InterruptionFrame` is a `SystemFrame`, so it jumps the queue. On interruption: processors cancel and
discard queued `DataFrame`s/`ControlFrame`s (but `FunctionCallResultFrame` and `EndFrame` are
uninterruptible and survive); the in-flight LLM completion is cancelled mid-stream; tools with
`cancel_on_interruption=True` are cancelled and emit `FunctionCallCancelFrame`; TTS drops pending
output; the output transport drains its audio queue
([`/pipecat/fundamentals/interruptions.md`](https://docs.pipecat.ai/pipecat/fundamentals/interruptions.md)).

**What lands in the context is only what was actually spoken** — `TTSTextFrame`s are pushed in sync
with audio playback, so text that never played never reaches the assistant aggregator. Observable via
`on_assistant_turn_stopped` (`message.interrupted`, `message.content`). This matters for transcript
fidelity in the Outcome.

**Controlling it**, in increasing severity:
`MinWordsUserTurnStartStrategy` → Krisp IP filter → user mute strategies
(`AlwaysUserMuteStrategy`, `FirstSpeechUserMuteStrategy`) → `enable_interruptions=False`.
The docs warn that disabling interruptions **does not ignore the Callee** — their speech is still
transcribed and answered once the bot finishes. To actually discard it, use mute strategies.
`FirstSpeechUserMuteStrategy` is the obvious candidate for protecting the Disclosure from being
talked over.

**Triggering an interruption ourselves**: `await self.broadcast_interruption()` from inside a
processor, or `await worker.queue_frame(InterruptionWorkerFrame())` from outside.

### Deepgram Flux vs VAD-plus-STT

Both are supported; they are different architectures, not different settings.

- Pipecat ships four Deepgram STT classes: `DeepgramSTTService`, `DeepgramFluxSTTService`, and
  SageMaker variants of each
  ([`/api-reference/server/services/stt/deepgram.md`](https://docs.pipecat.ai/api-reference/server/services/stt/deepgram.md)).
- **Flux does its own turn detection**, emitting `StartOfTurn`/`EndOfTurn`. It *proposes* boundaries
  rather than pushing turn frames, and **automatically requests `ExternalUserTurnStrategies` at
  start** so you do not configure turn strategies by hand. Pass your own `user_turn_strategies` only
  to override.
- Flux knobs: `eager_eot_threshold`, `eot_threshold`, `keyterm`, `should_interrupt`,
  `watchdog_min_timeout` (silence watchdog against dangling turns; the real threshold is
  `max(chunk_duration * 2, watchdog_min_timeout)`).
- With Flux, a VAD in the transport is **optional** — "not required for core turn management
  functionality, but it does enable useful STT metrics."
- **Trap**: if you set `should_interrupt=False` on the service *and* also pin
  `user_turn_strategies=ExternalUserTurnStrategies()` yourself, interruptions come back on, because
  the manual value replaces the service's recommendation. Pass
  `ExternalUserTurnStrategies(enable_interruptions=False)` or drop the manual arg. The aggregator
  logs a warning naming both fixes.

**Czech, unresolved.** This is the decision-relevant gap. `DeepgramSTTService` documents `language="multi"`
for multilingual streams. Flux's multilingual path is a *different* model, `flux-general-multi`, with
`language_hints=[Language.EN, Language.ES, Language.FR]` in the documented example. **The Pipecat docs
nowhere state whether `flux-general-multi` supports Czech**, and the hub index has no Deepgram
language matrix. This must be checked against Deepgram's own docs before Flux is chosen — it bears
directly on the map's "Language: Czech and English both, on one pipeline" decision and on
[05](../../.scratch/v1-end-to-end/issues/05-czech-and-english-on-one-pipeline.md).

Smart Turn v3 (the default stop strategy) "supports 23 languages, see the
[source repository](https://github.com/pipecat-ai/smart-turn)"
([smart-turn-overview](https://docs.pipecat.ai/api-reference/server/utilities/turn-detection/smart-turn-overview.md),
§"Notes"). **Whether Czech is one of the 23 is not stated in the Pipecat docs** — also worth checking.

**The trade-off, for the human to decide:**

| | Flux (STT-driven turns) | VAD + Smart Turn v3 (default) |
| --- | --- | --- |
| Turn boundary | Provider-side, purpose-built for agents; `eager_eot_threshold` lets the LLM start early | Local ONNX model on the audio + transcript |
| Config surface | Two thresholds; strategies auto-wired | Full strategy composition, `MinWords`, mute strategies, Krisp |
| Czech | **Unverified** (`flux-general-multi`) | **Unverified** (23 languages, list not in Pipecat docs) |
| Cost/dependency | Extra provider surface; SageMaker variant if self-hosting | Weights bundled in the package; no extra service |
| Fits the IVR case? | Turn boundaries are the provider's; IVRNavigator's `ivr_vad_params` knob assumes local VAD | IVRNavigator's documented `stop_secs=2.0`/`0.8` switching works directly |

The last row is a real coupling: `IVRNavigator` documents adjusting `VADParams(stop_secs=2.0)` for
menus and pushing `VADParamsUpdateFrame(VADParams(stop_secs=0.8))` for conversation. That knob is a
local-VAD knob. Combining Flux with `IVRNavigator` is not covered anywhere in the docs.

---

## 4. Voicemail / answering-machine detection

**Framework provides — this is a first-class extension, not something to build.**
`VoicemailDetector` from `pipecat.extensions.voicemail.voicemail_detector`; not deprecated at 1.8.1
(checked). Docs: [`/pipecat/fundamentals/voicemail.md`](https://docs.pipecat.ai/pipecat/fundamentals/voicemail.md),
implementation notes at
[`/api-reference/server/extensions/voicemail.md`](https://docs.pipecat.ai/api-reference/server/extensions/voicemail.md).

It is described as "built primarily for voice AI bots that perform outbound calling" — our exact case.
Architecture: a **parallel pipeline** running a separate cheap classifier LLM, plus a **TTS gate**.
The main pipeline generates its opening line immediately but the gate holds the audio until the
classification lands, so a live Callee sees no added latency and a machine never hears the wrong
thing.

Pipeline placement is two specific slots:

```python
pipeline = Pipeline(
    [
        transport.input(),
        stt,
        voicemail_detector.detector(),  # between STT and the user aggregator
        context_aggregator.user(),
        llm,
        tts,
        voicemail_detector.gate(),  # immediately after TTS
        transport.output(),
        context_aggregator.assistant(),
    ]
)
```

Constructor: `VoicemailDetector(llm=classifier_llm, voicemail_response_delay=2.0,
custom_system_prompt=...)`. A custom prompt must end with
`VoicemailDetector.CLASSIFIER_RESPONSE_INSTRUCTION`, which forces the response to be exactly
`CONVERSATION` or `VOICEMAIL`. **The main conversation LLM must be text-based** — realtime/S2S models
are not compatible there, though the classifier may be realtime with a `text` output modality.

Events: `on_voicemail_detected(processor)` and `on_conversation_detected`
([events overview](https://docs.pipecat.ai/api-reference/server/events/overview.md), §"Extensions").
The documented voicemail handler pushes a `TTSSpeakFrame` with the message and then
`EndWorkerFrame()` upstream. Working example:
[`examples/features/features-voicemail-detection.py`](https://github.com/pipecat-ai/pipecat/blob/5ff3201/examples/features/features-voicemail-detection.py).

**What is ours**: the *content* of the voicemail message (which must carry the Disclosure and name
the Principal), and mapping "left a voicemail" onto a Disposition. The `voicemail_response_delay`
default of 2.0s is tuned for US answering machines; Czech mobile voicemail greetings may need a
different value — that is an empirical setting, not a documented one.

---

## 5. DTMF, both directions

**Framework provides — both directions, first-class.**

### Receiving

`InputDTMFFrame` (a `SystemFrame`, carries `button: KeypadEntry`) is pushed by the transport.
`DTMFAggregator` from `pipecat.processors.aggregators.dtmf_aggregator` buffers presses and flushes a
`TranscriptionFrame` — marked `finalized=True` so it integrates with turn-stop strategies — when the
termination digit arrives (default `#`), on timeout (default 2.0s), on interruption, or on `EndFrame`
([`/api-reference/server/utilities/dtmf-aggregator.md`](https://docs.pipecat.ai/api-reference/server/utilities/dtmf-aggregator.md)).
Configurable: `timeout`, `termination_digit`, `prefix` (default `"DTMF: "`). Docs say to place it
**before** the user context aggregator. Note the first digit of a sequence calls
`broadcast_interruption()` (framework source: `dtmf_aggregator.py:97-113`).

### Sending

Two frame types in `pipecat.frames.frames`
(framework source `frames.py:855-903` and `frames.py:1576-1624`):

- `OutputDTMFFrame` — a `DataFrame`, **queued** behind pending audio
- `OutputDTMFUrgentFrame` — a `SystemFrame`, sent **immediately**

Both take `button=` (single) or `buttons=` (sequence), and `OutputDTMFFrame.from_string("1234#")`
builds the sequence from a dial string.

`BaseOutputTransport.write_dtmf()` dispatches: if the transport claims native DTMF it calls
`_write_dtmf_native()`, otherwise it falls back to `_write_dtmf_audio()`, which **synthesises the
tones as audio** and writes them into the media stream (`base_output.py:264-316`). So DTMF works on
any transport, with a fidelity difference: native (RFC2833/4733 `telephone-event`, or SIP INFO)
versus in-band generated tones.

Daily implements the native path (`DailyOutputTransport._write_dtmf_native`,
`daily/transport.py:2252`) and adds `DailyOutputDTMFFrame` with `session_id`, `digit_duration_ms` and
`method` (`"auto"` | `"telephone-event"` | `"sip-info"`), forwarded to Daily's `send_dtmf`
(`daily/transport.py:158-177`). There is also a direct `transport.send_dtmf({...})` call outside the
pipeline
([`/pipecat/telephony/daily-sip.md`](https://docs.pipecat.ai/pipecat/telephony/daily-sip.md),
§"Sending DTMF"). LiveKit surfaces received tones via `on_dtmf_event` and pushes `InputDTMFFrame`
([livekit transport ref](https://docs.pipecat.ai/api-reference/server/services/transport/livekit.md)).

Working example: [`examples/features/features-dtmf-menu.py`](https://github.com/pipecat-ai/pipecat/blob/5ff3201/examples/features/features-dtmf-menu.py)
("The caller interacts entirely with DTMF keypresses, no speech").

### IVR navigation — we probably do not need a `send_dtmf` tool at all

`IVRNavigator` from `pipecat.extensions.ivr.ivr_navigator` (not deprecated at 1.8.1) is a
goal-driven IVR walker: you give it a prose goal, it classifies IVR-vs-human, reads menus, and emits
DTMF and speech itself
([`/pipecat/fundamentals/ivr.md`](https://docs.pipecat.ai/pipecat/fundamentals/ivr.md)).
It goes in the pipeline **where the LLM would go** — it contains the LLM.

```python
ivr_navigator = IVRNavigator(llm=llm, ivr_prompt=goal, ivr_vad_params=VADParams(stop_secs=2.0))
pipeline = Pipeline([transport.input(), stt, ivr_navigator, tts, transport.output()])
```

Events:

- `on_conversation_detected(processor, conversation_history)` — a **human** answered. Carries the
  transcript so far, so you can build the conversation prompt on top of it and push
  `LLMMessagesUpdateFrame(messages=..., run_llm=True)`.
- `on_ivr_status_changed(processor, status)` with `IVRStatus.DETECTED` / `COMPLETED` / `STUCK`.
  `STUCK` is documented for exactly our failure modes: "required information (account numbers, PINs)
  isn't available", "menu options don't align with the stated goal".

On detecting an IVR it **automatically** switches system prompt and raises VAD `stop_secs` to 2.0 so
whole menu announcements are heard; the docs recommend pushing `VADParamsUpdateFrame(VADParams(stop_secs=0.8))`
when handing over to conversation.

**This is the single closest example to our v1**:
[`pipecat-examples/ivr-navigation/`](https://github.com/pipecat-ai/pipecat-examples/tree/87ede77/ivr-navigation)
— pinned `>=1.8.0`, dials out over Daily PSTN, uses `IVRNavigator`, and ends with an `end_call` direct
function. Its README's own framing is "the bot calls Daily Pharmacy… on behalf of Mark Backman" —
structurally the same errand as ours. Its `evals/` directory drives the whole navigation path over a
local WebSocket with **no phone call and no PSTN charges** (`bot.py -t eval`), with scenarios that
assert on DTMF the bot sends; this is directly relevant to
[11](../../.scratch/v1-end-to-end/issues/11-the-scripted-callee.md) and
[12](../../.scratch/v1-end-to-end/issues/12-build-the-eval-harness.md).

**Open question I could not resolve**: whether `VoicemailDetector` and `IVRNavigator` compose in one
pipeline. Both classify the first seconds of a call, from different slots (`detector()` sits between
STT and the user aggregator; `IVRNavigator` replaces the LLM), and both have a "a human answered"
event. **No doc or example combines them**, and the answer decides whether one call can distinguish
human / voicemail / IVR out of the box or whether we write the three-way classifier ourselves.

---

## 6. Detecting hold or silence without mistaking it for end-of-turn

Split this into two problems. Pipecat solves one and not the other.

### Silence — solved

Per-turn idle detection, off by default, enabled with one parameter
([`/pipecat/fundamentals/detecting-user-idle.md`](https://docs.pipecat.ai/pipecat/fundamentals/detecting-user-idle.md)):

```python
LLMUserAggregatorParams(user_idle_timeout=5.0)
```

The timer starts when the **bot** finishes speaking, cancels when either party speaks, and is
**suppressed during function calls and active user turns** — precisely so it does not misfire as an
end-of-turn signal. It fires `on_user_turn_idle(aggregator)`. The docs' own escalation pattern is
gentle prompt → direct prompt → `TTSSpeakFrame` goodbye + `EndWorkerFrame`, with a retry counter
reset on `on_user_turn_started`. The timeout is changeable mid-call with
`UserIdleTimeoutUpdateFrame(timeout=N)`, and `timeout=0` disables it; updates apply immediately.

That runtime knob is exactly what an Escalation stall needs: raise the idle timeout while waiting for
the Client to answer, restore it afterwards.

Distinct from the pipeline-level `idle_timeout_secs=300` on `PipelineWorker`, which cancels the whole
call.

### Hold music — **not** solved

**Searched and found nothing.** There is no audio-event classifier, no music detector, and no
"non-speech audio" frame in the index. The nearest things are noise *suppression* — `RNNoiseFilter`
and Krisp VIVA (`audio_filter = "tel"` for telephony up to 16 kHz) — which is the opposite problem.
The `daily-pstn` doc's "hold" is call-transfer hold, not hold music.

Consequences to reason about, not documented anywhere:

- Hold music is continuous audio. Silero VAD may or may not classify it as speech; if it does, the
  Callee "never stops speaking" and `user_turn_stop_timeout` (5.0s) becomes the only thing closing
  the turn. If it does not, the line looks silent and `on_user_turn_idle` fires — which at least
  gives us a hook, but with the wrong interpretation.
- STT on hold music will produce garbage transcripts that land in the LLM context.

This is a build item — see below.

---

## 7. Telephony transports available today

Two connection styles plus SIP, per
[`/pipecat/telephony/overview.md`](https://docs.pipecat.ai/pipecat/telephony/overview.md):

| Style | Providers | Call control | Docs |
| --- | --- | --- | --- |
| **WebSocket** (media streams) | Twilio, Telnyx, Plivo, Exotel (+ Vonage serializer exists) | "Basic, managed by the telephony provider"; explicitly **no transfers or reconnects** | [twilio-websockets](https://docs.pipecat.ai/pipecat/telephony/twilio-websockets.md), [telnyx-websockets](https://docs.pipecat.ai/pipecat/telephony/telnyx-websockets.md) |
| **WebRTC** | Daily, with PSTN bought through Daily | Advanced, track-level; cold and warm transfers | [daily-pstn](https://docs.pipecat.ai/pipecat/telephony/daily-pstn.md) |
| **SIP** (`provider="daily"`) | Daily SIP fronting Twilio, Telnyx, Plivo, Five9, Genesys, Cisco… | Full: transfers, forwarding, multi-party | [daily-sip](https://docs.pipecat.ai/pipecat/telephony/daily-sip.md), [twilio-daily-sip](https://docs.pipecat.ai/pipecat/telephony/twilio-daily-sip.md) |

The docs note you can run several vendors concurrently in one bot.

### What each demands of the surrounding process

**All of them need a long-lived HTTP server separate from the per-call bot** — this confirms the
architecture in `initial-research.md`.

**WebSocket (Twilio/Telnyx/Plivo/Exotel)** — for dial-out the server: (1) receives the request,
(2) creates the call via the provider's REST API with TwiML/equivalent that opens a WebSocket,
(3) accepts the inbound WebSocket, (4) parses call data and custom parameters, (5) runs the bot
([twilio-websockets](https://docs.pipecat.ai/pipecat/telephony/twilio-websockets.md),
§"Set up your server for dial-out"). Needs a public URL — the Telnyx guide names a tunnel like ngrok,
"for dial-out only". Fits the map's "laptop plus a tunnel" constraint.

Audio is **8 kHz**; the docs say to match it rather than resample:

```python
PipelineParams(audio_in_sample_rate=8000, audio_out_sample_rate=8000)
```

Encodings differ: Twilio and Plivo are 8 kHz mu-law (PCMU); Telnyx supports PCMU **and PCMA** via
`inbound_encoding`/`outbound_encoding` constructor args (PCMA is the EU norm); Exotel and Vonage are
raw 16-bit linear PCM.

**Daily PSTN / Daily SIP** — the server creates a room with `enable_dialout=True` and starts the bot;
the *bot* places the call from inside its `on_joined` handler via `transport.start_dialout({"phoneNumber": ..., "callerId": ...})`.
Events: `on_dialout_connected`, `on_dialout_answered`, `on_dialout_error`, plus `on_participant_left`
to shut down. The `ivr-navigation` example wraps this in a five-attempt retry loop keyed on
`on_dialout_error` and cancels the runner when they are exhausted
([`ivr-navigation/bot.py:56-151`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede77/ivr-navigation/bot.py)).
Gotchas the README calls out: a room created without `enable_dialout` fails with `unable to start
dialout: dial-out not enabled for room`, and a preset `DAILY_ROOM_URL` silently reuses a room that
lacks the property. Dial-out must also be enabled on the Daily domain. For SIP, an E.164 destination
needs dial-out approval; a `sip:` URI does not.

**Transport construction**: use `create_transport(runner_args, transport_params)` — it builds
telephony serializers for you. The runner-examples doc is blunt that telephony serializers "should
not [be] hand-roll[ed]"
([`runner-examples/01-create-transport-bot.py`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede77/runner-examples/01-create-transport-bot.py),
[`02-verbose-transport-bot.py`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede77/runner-examples/02-verbose-transport-bot.py)).
Our `pipecat-bot/bot.py` already uses `create_transport`, so adding telephony is adding a key to
`transport_params` — plus the extras: `pyproject.toml` currently installs
`pipecat-ai[cartesia,deepgram,openrouter,runner,silero,webrtc]`, with **no `daily` and no `twilio`/`telnyx`**.

`create_transport` also wires a headless **`eval` transport** for free (`-t eval`), needing no entry
in `transport_params`.

---

## 8. Injecting speech into a live call from outside — yes, supported

Directly relevant to the map's **stall-first Escalation** decision: the A2A executor needs to push the
Client's answer into a call already in progress.

Three documented paths, all from outside the pipeline via `worker.queue_frame(...)`
([`/api-reference/server/frames/overview.md`](https://docs.pipecat.ai/api-reference/server/frames/overview.md),
[`/pipecat/learn/context-management.md`](https://docs.pipecat.ai/pipecat/learn/context-management.md)):

1. **`LLMMessagesAppendFrame([message], run_llm=True)`** — appends to the context and triggers a
   completion immediately. This is the right one for an Escalation answer: the LLM sees the answer as
   context and phrases it in Czech or English, in the middle of the conversation, rather than reciting
   it. Pipecat Cloud's Session API documents this exact frame as the way to update a live
   conversation from an HTTP request
   ([session-api](https://docs.pipecat.ai/pipecat-cloud/guides/session-api.md)) — the same mechanism
   works in-process.
2. **`LLMMessagesUpdateFrame(messages=..., run_llm=True)`** — replaces the whole context. This is what
   `IVRNavigator`'s `on_conversation_detected` handler uses to switch from navigation to conversation.
   `LLMMessagesTransformFrame` edits in place.
3. **`TTSSpeakFrame("…")`** — sends text straight to TTS, bypassing the LLM. Use for fixed lines. Our
   `pipecat-bot/bot.py` already does this in `on_client_connected`. Good for stall filler ("let me
   check with them, one moment") where determinism matters.

Also available from outside: `LLMSetToolsFrame` (change the tool set), `VADParamsUpdateFrame`,
`UserIdleTimeoutUpdateFrame`, `LLMUpdateSettingsFrame` / `TTSUpdateSettingsFrame` /
`STTUpdateSettingsFrame`, `InterruptionWorkerFrame`, `ManuallySwitchServiceFrame`.

**No polling, no custom channel needed.** The `PipelineWorker` handle is an ordinary Python object;
the A2A executor holds it and queues frames.

---

## 9. Known production traps — mostly fixed since the older research

### Errors and failover — now first-class

The whole error model was rebuilt around one question: *can this processor still do its job?*
([`/pipecat/fundamentals/error-handling.md`](https://docs.pipecat.ai/pipecat/fundamentals/error-handling.md))

- **`is_usable`** on every `FrameProcessor`. A processor stays usable through transient failures and
  becomes unusable on permanent ones. Unusable STT/TTS stop accepting work; unusable WebSocket
  services stop reconnecting.
- **`ErrorCategory`**: `AUTHENTICATION`, `AUTHORIZATION`, `INVALID_REQUEST`, `RATE_LIMIT`, `QUOTA`,
  `CONNECTIVITY`, `SERVER`, `APPLICATION`, `UNKNOWN`. The first three are permanent — read
  `category.is_permanent`. HTTP statuses map automatically (401→AUTH, 403→AUTHZ, 429→RATE_LIMIT,
  5xx→SERVER).
- **`PipelineWorker(processor_unusable_policy=...)`**: `CONTINUE` (default), `END`, `CANCEL`. Applied
  once per processor, not per failed request. The Pipecat examples use `END`.
- Events: `on_error(processor, frame)`, `on_usable_changed(processor, is_usable)`. Recover with
  `await stt.set_usable(True)`.
- **Deprecated**: `ErrorFrame.fatal`, `push_error(fatal=True)`, `FatalErrorFrame` — removed in 2.0.0.

**`ServiceSwitcher`** ([service-switcher](https://docs.pipecat.ai/api-reference/server/utilities/service-switchers/service-switcher.md))
is the built-in failover, a parallel pipeline where filters gate all but the active service:

```python
switcher = ServiceSwitcher(
    services=[primary, backup], strategy_type=ServiceSwitcherStrategyFailover
)
```

`ServiceSwitcherStrategyFailover` ignores recoverable errors and switches on unusability, wrapping
around the list. A service that failed to connect during setup is already unusable, "so the switch
happens before the pipeline starts and every frame reaches a service that connected." Recovery is the
application's call, via `set_usable(True)` in `on_service_switched`. `ServiceSwitcherStrategyManual`
(the default) switches on `ManuallySwitchServiceFrame`. Custom strategies override `handle_error`.
There is also an `LLMSwitcher`, across which tools listed in `LLMContext(tools=[...])` remain
available on whichever provider is active.

### Websocket reconnection — automatic

`WebsocketService` (framework source `services/websocket_service.py:84-412`) provides "automatic
reconnection with exponential backoff, connection verification, and error handling", with
`_try_reconnect(max_retries=3)`, `send_with_retry`, and a `QuickFailureTracker`. Deepgram's page
states the concrete rule: "After three consecutive attempts fail to produce a connection that stays
up, the service stops reconnecting and reports itself unable to do its job, so a `ServiceSwitcher`
moves off it."

Service-level events for observability: `on_connected`, `on_disconnected`, `on_connection_error`
([service-events](https://docs.pipecat.ai/api-reference/server/events/service-events.md)).

### Cold start and service init order

- **Services set up concurrently, not sequentially.** The `StartupTimingObserver` report defines
  `total_duration_secs` as "wall-clock span from the pipeline starting to set up until it had
  started. **Processors set up concurrently, so this is the span, not the sum** of what each cost"
  ([startup-timing-observer](https://docs.pipecat.ai/api-reference/server/utilities/observers/startup-timing-observer.md)).
- **Smart Turn weights ship in the package.** "The model weights are bundled with Pipecat, so there's
  no need to download them separately"
  ([pipecat-cloud/guides/smart-turn](https://docs.pipecat.ai/pipecat-cloud/guides/smart-turn.md)).
  `LocalSmartTurnAnalyzerV3`'s `smart_turn_model_path` is optional and only overrides the bundled
  copy. V3 "supports fast CPU inference on ordinary cloud instances."
- **Measure rather than guess**: `StartupTimingObserver` gives per-processor `setup_duration_secs`
  (the connecting part) and `duration_secs`, plus `on_transport_timing_report` with
  `bot_connected_secs` (SFU transports only — Daily, LiveKit; `None` for WebSocket/SmallWebRTC) and
  `client_connected_secs`. Filterable with `processor_types=(STTService, TTSService)`.

I found **no** current doc supporting a "3–8 s cold start because services initialise sequentially and
Silero downloads on first run" claim. Silero's on-disk footprint at first run is not documented in the
index — if it matters for the tunnel-on-a-laptop deployment, measure it with the observer.

---

## Corrections to `initial-research.md`

Three claims in [`docs/initial-research.md`](../initial-research.md) §"Pipecat is the right voice
pipeline" no longer hold at 1.8.1:

| Claim there | Status |
| --- | --- |
| "there is no built-in LLM `FallbackAdapter` (build failover with `ParallelPipeline`)" | **Wrong.** `ServiceSwitcher` + `ServiceSwitcherStrategyFailover` is the supported mechanism, built on `is_usable`. `ParallelPipeline` failover is still shown in the docs but is the manual route |
| "enable `reconnect_on_error=True` for WebSocket TTS" | **Gone.** No such option in the 1.8.1 docs or API index. Reconnection with exponential backoff is unconditional in `WebsocketService`, bounded by three consecutive failures, and terminates into `is_usable=False` |
| "Pipecat services initialize sequentially, adding a 3–8 s cold start (it downloads the Silero VAD model on first run)" | **At least partly wrong.** Processors set up *concurrently*; Smart Turn weights are bundled in the package. Measure with `StartupTimingObserver` |

Also outdated across that doc's code sketches (all deprecated 1.3.0/1.4.0, removal in 2.0.0):
`PipelineTask` → `PipelineWorker`, `task.cancel()` → `worker.cancel()`, `EndTaskFrame` →
`EndWorkerFrame` (verified via `check_deprecation`), `CancelTaskFrame` → `CancelWorkerFrame`,
`OpenAILLMContext` + `llm.create_context_aggregator()` → `LLMContext` +
`LLMContextAggregatorPair`. `pipecat-bot/bot.py` already uses the current names.

Its `phone-chatbot/daily-twilio-sip-dial-out` recommendation is superseded for our purposes by
`ivr-navigation/`, which is pinned `>=1.8.0` and is the same errand shape.

---

## Summary: framework vs. us

### The framework provides

| Ticket topic | Component |
| --- | --- |
| Function calling | Direct functions, `@tool_options`, `LLMSetToolsFrame`, `app_resources` |
| Graceful hang-up from inside | `EndWorkerFrame` from a tool; serializer `auto_hang_up` ends the PSTN leg |
| End-of-call write point | `on_pipeline_finished` |
| Turn-taking / barge-in | `UserTurnStrategies`, `MinWordsUserTurnStartStrategy`, mute strategies, Krisp IP |
| Turn detection | Smart Turn v3 (bundled) or Deepgram Flux (`ExternalUserTurnStrategies` auto-wired) |
| Voicemail | `VoicemailDetector` — parallel classifier + TTS gate + `on_voicemail_detected` |
| IVR menus | `IVRNavigator` — goal-driven, `on_conversation_detected`, `IVRStatus.STUCK` |
| DTMF | `InputDTMFFrame` + `DTMFAggregator`; `OutputDTMFFrame`/`OutputDTMFUrgentFrame`, native or synthesised tones |
| Silence | `user_idle_timeout` + `on_user_turn_idle` + `UserIdleTimeoutUpdateFrame` |
| Telephony | Twilio/Telnyx/Plivo/Exotel WebSocket, Daily PSTN, Daily SIP; `create_transport` builds serializers |
| Speech injection from outside | `LLMMessagesAppendFrame(run_llm=True)`, `LLMMessagesUpdateFrame`, `TTSSpeakFrame` |
| Failover | `ServiceSwitcher` + `ServiceSwitcherStrategyFailover`, `is_usable`, `ErrorCategory`, `processor_unusable_policy` |
| Reconnection | `WebsocketService`, automatic, exponential backoff, 3-failure cap |
| Cold-start diagnosis | `StartupTimingObserver` |
| Phone-free regression runs | `-t eval` transport + `pipecat eval suite`, with `dtmf:` turns in scenarios |

### What we must build

1. **The Outcome contract and its emission.** A `report_outcome`-style direct function writing into
   `app_resources`, read by the A2A executor at `on_pipeline_finished`. Framework gives the seam, not
   the shape.
2. **Disclosure delivery and protection.** Speaking it first, in the right language, and probably
   `FirstSpeechUserMuteStrategy` so it cannot be talked over. Nothing in Pipecat knows about Art. 50.
3. **Escalation orchestration.** Pipecat gives the injection primitives; the stall-then-call-back
   policy, the timeout on waiting for the Client, and raising/restoring `user_idle_timeout` during a
   stall are ours ([03](../../.scratch/v1-end-to-end/issues/03-escalation-policy.md)).
4. **Hold-music handling.** No framework support at all. Would need a custom `FrameProcessor` — or a
   prompt-level convention plus a raised `user_idle_timeout` — and is a genuine unknown until a real
   line is met.
5. **The three-way answer classification (human / voicemail / IVR)**, if `VoicemailDetector` and
   `IVRNavigator` turn out not to compose.
6. **Maximum call duration.** Documented as an application-level `asyncio` timer, not a parameter.
7. **Czech-language verification** for whichever turn-detection route is chosen.
8. **Language switching** across Czech and English on one pipeline — out of scope here, see
   [05](../../.scratch/v1-end-to-end/issues/05-czech-and-english-on-one-pipeline.md).

### Could not establish — flagged, not guessed

- **Whether Deepgram Flux (`flux-general-multi`) supports Czech.** Pipecat's docs list only EN/ES/FR
  in the example. Needs Deepgram's own docs.
- **Whether Smart Turn v3's 23 languages include Czech.** The list is not in the Pipecat docs; it
  points at `github.com/pipecat-ai/smart-turn`.
- **Whether `VoicemailDetector` and `IVRNavigator` can share a pipeline.** No doc, no example, no
  statement either way.
- **Whether `IVRNavigator` works with Deepgram Flux.** Its documented VAD-tuning behaviour
  (`stop_secs` 2.0 ↔ 0.8) assumes local VAD; Flux takes turn control away from it.
- **Silero VAD first-run download size / time.** Not in the index; the "3–8 s cold start" figure in
  `initial-research.md` is uncorroborated here. Measure with `StartupTimingObserver`.
- **How hold music affects Silero VAD.** No documented behaviour; empirical.
