# What Pipecat Evals can assert, and what it can't

Research for [.scratch/v1-end-to-end/issues/06-what-pipecat-evals-covers.md](../../.scratch/v1-end-to-end/issues/06-what-pipecat-evals-covers.md).
Facts only; choices are left to the human.

**Version pinned**: `pipecat-ai` **1.8.1** (this repo's floor, `pyproject.toml`). Every source-code
claim below is read from the `v1.8.1` tag, not from `main` and not from `.venv`.
Example scenarios are read from `pipecat-ai/pipecat-examples` at commit
`87ede7793edc6b9b49797dc8d3bc6cb51f6f9252`.

Vocabulary follows [CONTEXT.md](../../CONTEXT.md). Note one unavoidable collision: Pipecat Evals
calls the scripted side of a scenario the **`user:`**. In our terms that party is the **Callee** —
the restaurant. Wherever this document says `user:` in code voice, it means the Callee's lines.

---

## 1. Shape of a run

Two halves, both first-party.

**The eval transport.** The bot under test is started with `-t eval`. It then hosts a local
WebSocket server speaking RTVI instead of connecting to Daily/WebRTC/telephony
([overview](https://docs.pipecat.ai/pipecat/evals/overview.md)). Selection happens inside
`create_transport()`: the runner builds `EvalRunnerArguments(host="localhost", port=7860)` and
`create_transport` branches on that type
([`runner/types.py` L252-266](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/runner/types.py),
[`runner/utils.py` L716-740](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/runner/utils.py)).

**The eval harness.** `pipecat eval run` (or the `pipecat.evals` library) connects to that socket as
an RTVI client, plays the scenario's turns, collects the semantic events the bot emits, and asserts
on them in order.

```bash
uv run bot.py -t eval                    # terminal 1: bot, headless, ws://localhost:7860
pipecat eval run scenarios/booking.yaml -v   # terminal 2: the scenario
```

Exit code is `0` on all-pass, `1` otherwise. Each scenario writes a timestamped decision trace to
`<scenario>.eval.log` ([quickstart](https://docs.pipecat.ai/pipecat/evals/quickstart.md)).

`pipecat eval suite manifest.yaml` goes further: it *spawns* one fresh bot process per scenario on
its own port, runs them concurrently, tears them down, and writes `results.jsonl` — one line per run
with bot, scenario, attempt, outcome, duration, failures, per-turn results and artifact paths
([suites](https://docs.pipecat.ai/pipecat/evals/suites.md)).

### What the bot must look like

Three requirements, all of them cheap:

1. **A `"eval"` entry in `transport_params`.** `create_transport` raises unless the value is an
   `EvalTransportParams` instance:

   ```python
   from pipecat.evals.transport import EvalTransportParams

   transport_params = {
       "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
       "eval": lambda: EvalTransportParams(audio_in_enabled=True, audio_out_enabled=True),
   }
   ```

   > **Doc/source discrepancy.** The published quickstart shows
   > `"eval": lambda: SingleClientWebsocketServerParams(...)`. At v1.8.1 `create_transport` does
   > `if not isinstance(params, EvalTransportParams): raise ValueError(...)`, and
   > `EvalTransportParams` is a *subclass* of `SingleClientWebsocketServerParams` — so the
   > documented snippet fails the check. Use `EvalTransportParams`.
   > ([`runner/utils.py` L725-731](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/runner/utils.py),
   > [`evals/transport.py` L206-216](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/transport.py))

2. **An RTVI processor and observer.** Free: `PipelineWorker.__init__` takes `enable_rtvi: bool = True`
   and prepends an `RTVIProcessor` plus its observer when no external one is found
   ([`pipeline/worker.py` L296, L485-513](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/pipeline/worker.py)).
   This repo's `pipecat-bot/bot.py` already qualifies.

3. **Nothing else.** Same pipeline, same services, same event handlers. The conversation layer never
   learns which transport it is on — which is exactly the seam
   [02 — Where the seams go](../../.scratch/v1-end-to-end/issues/02-where-the-seams-go.md) is
   about. The eval transport is chosen by a CLI flag at process start, above `run_bot()`.

### What the eval transport does differently

Per-connection query flags the harness sets
([`evals/transport.py` docstring](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/transport.py)):

| Flag | Effect |
| --- | --- |
| `?skip_tts=true` | Silences the bot's output for the session (text mode). Pushed as an `LLMConfigureOutputFrame` **before** `on_client_connected` fires, so even an on-connect greeting is silent. |
| `?user_audio=true` | Enables a **virtual microphone**: the harness's `raw-audio` messages are played into the pipeline at real-time cadence (20 ms frames) with locally generated silence between utterances, so VAD, turn detection and streaming STT see what a live mic would produce. Off in text mode — no silence is ever fed to the bot's STT, so text mode costs no streaming-STT minutes. |
| `?capture_bot_audio=true` | Forwards the bot's synthesized audio to the harness for `tts_response` transcription. |
| `?record=<path>` | Records composite (Callee + bot) conversation audio to a WAV via an `AudioBufferProcessor` placed after the output transport. Written on disconnect, *before* `on_client_disconnected` fires. |
| `?trigger_disconnect` | Fires the bot's `on_client_disconnected` handler when the connection ends. |

By default `on_client_disconnected` is **suppressed** so one bot process can serve several
scenarios back to back. Our bot calls `runner.cancel()` in that handler, so `trigger_disconnect:
true` on a scenario ends the bot process — treat such a scenario as terminal, last in a list.

---

## 2. The assertion vocabulary

This is the complete list at v1.8.1, read from
[`evals/scenario.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/scenario.py)
(`EvalExpectation`, L272-306) rather than from the docs, which omit `absent:` and the `vad_*` events.

### Events you can assert on

| Event | Meaning | Available in |
| --- | --- | --- |
| `response` | The bot's reply. Modality-agnostic alias: resolves to `llm_response` in text mode, `tts_response` (transcription of real synthesized audio) in audio mode. **Prefer this.** | both |
| `llm_response` | The LLM's raw text output for the turn. | both |
| `tts_response` | Text the TTS reports speaking, one segment at a time, with word timing. | audio only |
| `llm_started` | The LLM began generating. | both |
| `function_call` | The LLM called a tool. | both |
| `function_call_stopped` | A tool call ended; its `args` carry `tool_call_id` and `cancelled`. | both |
| `user_transcription` | The bot's STT finalized a transcription of the Callee. | audio, or any DTMF turn |
| `user_started_speaking` / `user_stopped_speaking` | Turn-level speech boundaries. | audio, or any DTMF turn |
| `vad_user_started_speaking` / `vad_user_stopped_speaking` | Raw VAD signal, useful as a timing anchor when a turn-detection strategy defers the turn-level event. Undocumented in the guide; present in the source. | audio |

### Assertion fields

Exactly six, per expectation:

```yaml
- event: response        # required
  within_ms: 2000        # latency budget, measured from the turn's send
  text_contains: "Berlin"  # plain substring, no judge round-trip
  eval: "the reply confirms the booking"   # judged criterion (judgeable events only)
  calls: [...]           # function_call / function_call_stopped only
  absent: true           # invert: assert NO such event arrives within the budget
```

- **`eval:`** is only meaningful on `response`, `llm_response`, `tts_response`
  (`JUDGEABLE_EVENTS`); anything else produces a parser warning.
- **`absent: true`** matches on event type only and cannot be combined with `text_contains`,
  `eval:` or `calls:`. Set `within_ms` explicitly or you pay the 60 s default as a quiet-window wait.
- **`calls:`** matches by name in **any order**; the expectation passes only when all listed calls
  are found. `args` is a **subset** check — listed key/values must be present, extras ignored. A
  single call can use the `name:`/`args:` shorthand directly on the expectation; a bare
  `function_call` asserts only that *some* call happened.
- **Ordering**: expected events must arrive in the order listed, but the bot may emit others in
  between, so you need not enumerate everything.
- **`within_ms` anchoring**: *all* of a turn's expectations share one deadline anchored at the
  moment the turn's input was **sent**. Time spent matching earlier expectations counts against
  later ones. Default 60 s (`--timeout` changes it). For audio turns the anchor is when the
  utterance was sent, **not** when it finishes playing out of the virtual mic — see §8.

### Turn drivers

| Field | Meaning |
| --- | --- |
| `user: "<string>"` | The Callee's utterance for this turn. A plain string. |
| `dtmf: "123#"` | Keypad presses instead of speech; mutually exclusive with `user:`. One `InputDTMFFrame` per character (`0`-`9`, `*`, `#`), injected the same way in either modality. Quote it — an unquoted `#` starts a YAML comment. |
| *(neither)* | Observation-only turn — waits for and asserts on events. This is how you test the bot's on-connect Disclosure. |
| `send_after: {event: llm_started, delay_ms: 2000}` | Schedules the send relative to a prior event (barge-in tests) or, with no `event:`, as a pure delay from the previous send. |
| `image: assets/x.jpg` | Serves an image when a vision bot requests one. |
| `expect:` | Optional. Omit for a pure pacing turn. |

### Scenario-level fields

| Field | Default | Meaning |
| --- | --- | --- |
| `name:` | — | required |
| `turns:` | — | required |
| `context:` | *(none)* | Messages that **replace** the bot's LLM context wholesale, via `LLMMessagesUpdateFrame`, sent right after the bot-ready handshake. Lets a scenario start mid-conversation. Omit and the bot keeps its own context. |
| `judge:` | ollama/gemma4:12b, text | See §3. |
| `user:` | text | See §4. |
| `stop_on_failure:` | `true` | First failed turn ends the scenario. Set `false` to score every turn independently (`EvalTurnResult.status` per turn; `not_run` is deliberately distinct from `passed`). |
| `trigger_disconnect:` | `false` | Fire the bot's disconnect handler when this scenario ends. |
| `!include <file>` | — | Any value can be pulled from a sibling file — how the `ivr-navigation` example shares one `judge_text.yaml` across scenarios. |

### Prior art: what upstream actually asserts

**Judged conversational quality** — `phonellm/server/evals/scenarios/naturalness.yaml`
([source](https://github.com/pipecat-ai/pipecat-examples/blob/87ede7793edc6b9b49797dc8d3bc6cb51f6f9252/phonellm/server/evals/scenarios/naturalness.yaml)).
The rubric lives inline, one criterion per expectation, phrased as prose with an explicit failure
clause:

```yaml
  - expect:
      - event: response
        eval: >-
          a short greeting that offers help with a reservation. It asks at most
          one question.

  - user: "Hey. Can I make a reservation for a party of two?"
    expect:
      - event: response
        eval: >-
          asks for the details still needed to book — BOTH the caller's name AND
          the day/time — together in this single reply. Asking for only one of
          them, and leaving the other for a later turn, is a failure.

  - user: "Yeah, it's Mark. Do you have anything for tomorrow at seven PM?"
    expect:
      - event: response
        eval: >-
          confirms the booking details back to the caller. Any reference to the
          date is short, such as "tomorrow" or an ordinal like "the 29th". The
          reply must NOT contain a four-digit year such as 2026, and must NOT
          contain a numeric date such as 2026-08-29.

  - user: "Yeah, that's right."
    expect:
      - event: function_call
        name: create_reservation
        args: { name: "Mark", party_size: 2 }
      - event: response
        eval: >-
          gives the caller the confirmation number. It does not spell the date
          out in full again […]

  - user: "No, that's all. Thanks."
    expect:
      - event: response
        eval: >-
          a brief, friendly goodbye of roughly one sentence. It does not ask a
          new question or offer further help.
      - event: function_call
        name: end_call
```

Note the ordering comment in the original: the goodbye is asserted *before* `end_call`, because
that is the order the bot produces them, and nothing follows `end_call` to match against — it shuts
the pipeline down. The same trap applies to our hang-up.

**Deterministic tool exercise** — `reservations.yaml` uses no `eval:` at all: `function_call` with
`name`/`args` plus a bare `event: response` to wait for the reply. This is the pattern for anything
that must not depend on a judge.

**Negative assertion** — `ivr-navigation/evals/scenarios/human_answers.yaml`
([source](https://github.com/pipecat-ai/pipecat-examples/blob/87ede7793edc6b9b49797dc8d3bc6cb51f6f9252/ivr-navigation/evals/scenarios/human_answers.yaml)):

```yaml
judge: !include judge_text.yaml

turns:
  - user: "Daily Pharmacy, this is Sarah speaking. How can I help you today?"
    expect:
      - event: response
        text_contains: "<mode>conversation</mode>"
      - event: response
        absent: true
        within_ms: 10000
```

That is the shape for "and then the bot shut up" — directly reusable for *the agent did not keep
talking after the Disclosure*, or *the agent did not invent a follow-up after the Callee declined*.

> **Caution when copying `naturalness.yaml` / `phonellm_smoke.yaml`:** both have a YAML indentation
> bug in the `judge:` block (`eval:` and `service:` at the same indent), which parses as
> `{eval: null, service: ollama, ...}`. `_parse_judge_block` then does
> `eval_cfg = judge.get("eval") or dict(_DEFAULT_JUDGE)`, so it silently falls back to the default
> judge and the sibling `service`/`model`/`extra` keys are ignored. It only looks correct because
> the default *is* ollama/gemma4:12b. `reservations.yaml` has it right.

---

## 3. The judge

**Where it runs.** `EvalJudge` wraps any Pipecat LLM service with an OpenAI-compatible API and
calls `run_inference()` **out of pipeline**, one shot per assertion
([`evals/judge.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/judge.py)).

**Configuration.** Defaults are `service: ollama`, `model: gemma4:12b`,
`extra: {reasoning_effort: none}` (`DEFAULT_OLLAMA_JUDGE_MODEL` / `DEFAULT_OLLAMA_JUDGE_EXTRA` in
`evals/services.py`). Built-in services: `ollama`, `openai`, or any OpenAI-compatible endpoint via
`endpoint:`. A `factory:` dotted path is the escape hatch to any other service.

```yaml
judge:
  modality: text          # text | audio
  eval:
    service: openai
    model: gpt-4o-mini
    # endpoint: https://...    # any OpenAI-compatible endpoint
    # extra: { ... }           # forwarded as top-level request params
```

Gotcha from the docs: naming a `model:` without an `extra:` **drops** the defaults rather than
merging, because they are chosen for the default model.

**How a rubric is expressed.** There is no rubric object, no scoring scale, no weights. A rubric is
**one natural-language string per expectation**, and the verdict is boolean. The judge's own system
prompt (`JUDGE_SYSTEM_INSTRUCTION`) is fixed in the framework and not configurable from YAML. It:

- sees the **whole conversation so far** as an `LLMContext`, so a terse reply ("That's four") is
  resolvable;
- is told the assistant text may be an STT transcription and to judge *intended spoken meaning*,
  never spelling — explicitly, "for"/"fore" = four, "to"/"too" = two;
- returns strict single-line JSON: `{"verdict": "yes" | "no" | "continue", "reason": "<one short sentence>"}`;
- uses **`continue`** for interim replies ("Let me check on that") — the harness keeps accumulating
  response text and re-judges until the criterion is met or the time budget runs out. `no` is only
  for a substantive answer that fails.

`JudgeVerdict.passed` is `verdict == "yes"` — so **`continue` never passes**; it just buys more
time. Verdicts are cached by `(criterion, conversation)` hash, so a re-run is stable and one
assertion never pays two round-trips. `max_tokens` defaults to 200.

**Can it carry the criteria from [10 — What we measure](../../.scratch/v1-end-to-end/issues/10-what-we-measure.md)?**
Each of the five is expressible as an `eval:` string on a `response`:

| Criterion | Expressible? | Notes |
| --- | --- | --- |
| Disclosure present **and early** | Yes | "Early" is structural, not judged: put it on the **first, observation-only turn**, so anything later cannot satisfy it. That is a hard gate for free. |
| One question at a time | Yes | Directly the `naturalness.yaml` pattern, including its "asking for only one of them […] is a failure" phrasing. |
| Confirmed details before hanging up | Yes | Assert the confirming `response` **before** the `end_call` function call, per the ordering note above. |
| No invented decisions | Partly | A judge can be told "does not agree to a time the Callee never offered", but this is a *whole-call* property; per-turn judging sees only the conversation up to that turn (which is enough, since the judge gets full prior context) — but a single "nothing anywhere in the call was invented" assertion has no natural anchor turn. |
| Summary faithful to the call | **No, not in-band** | The `summary` prose lives in the **Outcome**, which is returned over A2A, not emitted as an RTVI event. Pipecat Evals has no event for it. See §8. |

Because `eval:` is boolean, **there are no scores** — every judged criterion is a hard gate or it is
not asserted. A per-criterion 1-5 rating, weighted scoring, or a "3 of 5 must pass" rule has to be
computed outside the harness, from `EvalResult.turns` / `results.jsonl`.

**Checking the judge itself** is entirely on us; the framework offers nothing. The materials it
gives are: `--repeat N` for pass-rate over N attempts, and the `.eval.log` decision trace which
records the judge's `reason` for every verdict.

---

## 4. Text mode versus audio mode

Two **independent** switches. `user.modality` sets how the Callee's line reaches the bot;
`judge.modality` sets what the judge reads. Either can be `text` or `audio`; both default to `text`,
and a scenario with neither block is fully text mode.

| | `modality: text` | `modality: audio` |
| --- | --- | --- |
| **`user:` (Callee → bot)** | RTVI `send-text`. Bypasses the bot's **VAD, turn detection and STT** entirely. No audio ever crosses the wire, so no streaming-STT minutes are billed. | Synthesized by a TTS the *harness* runs, streamed through the virtual mic at real-time cadence. Exercises VAD, turn detection and STT for real. Synthesized audio is **cached across runs**, keyed on `(service, voice, model, language)` + text. |
| **`judge:` (bot → judge)** | The bot's **TTS is skipped** (`skip_tts`), including the on-connect greeting. `response` resolves to `llm_response`. Fast and silent. | The bot speaks for real; the harness captures the audio, transcribes it with a local STT, and `response` becomes that transcription — what a Callee would actually have heard. |

Mixing is legal and sometimes what you want: drive with text, judge real speech (or vice versa).

**Harness-side services** (these are the *harness's* TTS/STT, distinct from the bot's):

```yaml
user:
  modality: audio
  speech:
    service: kokoro        # local ONNX, no key, no per-run cost; or `cartesia` (HTTP)
    voice: af_heart        # voices are language-specific
    language: cs           # optional; a code or a Language
    sample_rate: 16000     # optional, default 16000

judge:
  modality: audio
  transcription:
    service: whisper       # or `moonshine` (the default)
    model: distil-medium   # non-English needs a multilingual model
    language: cs
    padding_secs: 2        # silence padded around each segment; 0 disables
```

Constraints read from source:

- Built-in TTS: **`kokoro`, `cartesia`** only. Built-in STT: **`moonshine`, `whisper`** only. Both
  blocks take a `factory:` dotted path (`(cfg, sample_rate) -> service`) for anything else.
- **WebSocket-streaming services are rejected** — local models or HTTP only. They need a running
  pipeline to manage their connection lifecycle. This rules out streaming Deepgram/ElevenLabs *on
  the harness side*; the bot's own services are untouched.
- The judge transcriber **always resamples to 16 kHz** internally
  (`transcribe.py`: `if sample_rate != STT_SAMPLE_RATE: resample(...)`).
- Docs warn explicitly: prefer `whisper` over `moonshine` for a non-English bot — Moonshine's
  non-English models return empty or truncated transcripts, and *an empty transcript is
  indistinguishable from a bot that said nothing*.
- An unrecognized `language` code raises `ValueError` naming it. Voices are **not** selected for
  you: `af_heart` speaks US English whatever `language` says.

**Which axis needs which** (mapping onto [10](../../.scratch/v1-end-to-end/issues/10-what-we-measure.md)):

- **Task success on scripted calls** — text mode. Function-call assertions plus judged prose; no
  audio cost, seconds per scenario, deterministic enough to gate on.
- **Judged transcript and outcome quality** — text mode for the transcript half. Turn structure,
  Disclosure, one-question-at-a-time, confirmation ordering are all LLM-text properties.
- **Latency and speech quality / Czech narrowband** — audio mode is *necessary* but not
  *sufficient*; see §8.

---

## 5. Can a `user:` turn react to the bot? No.

This is the collision named in
[11 — The scripted callee](../../.scratch/v1-end-to-end/issues/11-the-scripted-callee.md), and the
answer from source is unambiguous.

- `EvalTurn.user` is `str | None`, parsed from YAML at load time
  ([`evals/scenario.py` L334-372](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/scenario.py)).
- The run loop is `for turn_idx, turn in enumerate(self._scenario.turns)` and sends `turn.user`
  verbatim ([`evals/harness.py` L559, L1064-1079](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/harness.py)).
- `EvalSession.from_scenario` accepts injections for `judge=`, `speech=`, `transcriber=` — **not**
  for the turns. There is no per-turn generation callback anywhere in the API.
- Building scenarios in Python (`EvalScenario(name=..., turns=[EvalTurn(user="...")])`) changes
  nothing: the strings are still fixed before `run()` is awaited.

**The only dynamism the framework offers is timing, not content**: `send_after:` can anchor a send
to an event (`llm_started`) plus a delay. So "the Callee interrupts 2 s into the agent's answer" is
expressible; "the Callee decides, based on what the agent just said, that it will not wait" is not.

There is one mechanically-available but **unsupported** hook worth naming honestly rather than
discovering later: `on_progress` fires with `EvalTurnProgress(status="matched", detail=<matched
event's text>)` as each expectation resolves, and the harness reads `turn.user` lazily at send time.
A Python driver could therefore mutate `scenario.turns[n+1].user` from inside `on_progress`. That is
reaching into a dataclass the framework treats as immutable input; it is not an extension point, and
nothing in the docs sanctions it.

**Consequence for ticket 11**, stated as fact and not as a recommendation:

- Scenarios whose Callee behaviour is a *fixed sequence* — cooperative booking, fully booked, wrong
  number, voicemail, "only a later slot is available" *when the offer is scripted* — are fully
  served by YAML. That is most of the named v1 set.
- Scenarios where the Callee's next line **depends on what the agent did** need something outside
  Pipecat Evals. The named example is exactly right: *the Callee deciding it will not wait for an
  Escalation answer depends on how long the agent stalls*. `send_after: {event: llm_started,
  delay_ms: N}` can fake the *timing* of that decision, but the Callee's line is still chosen in
  advance, so the scenario asserts the agent's handling of a decision it did not actually provoke.

---

## 6. Tracing: does an eval run emit OpenTelemetry?

**Yes — if and only if the bot is configured to trace, and the eval transport changes nothing about
that.** Tracing is set up in the bot, not in the transport or the harness:

1. `setup_tracing(service_name=..., exporter=..., console_export=...)` installs the `TracerProvider`.
2. `PipelineWorker(..., enable_tracing=True, enable_turn_tracking=True, params=PipelineParams(enable_metrics=True))`
   turns it on for that pipeline
   ([OpenTelemetry guide](https://docs.pipecat.ai/api-reference/server/utilities/opentelemetry.md)).

Neither step reads the transport. So a scenario run under `-t eval` produces the same
`conversation → turn → {stt, llm, tts}` span tree as a real call, with the same attributes
(`metrics.ttfb`, `gen_ai.usage.*`, `turn.duration_seconds`, `turn.was_interrupted`, LLM `input`/`output`, …).

**Three qualifications that matter:**

- **Text mode changes the trace shape.** No audio reaches the bot, so its STT never runs; `skip_tts`
  silences its TTS. A text-mode eval trace therefore has **`llm` spans but no `stt` or `tts` spans**,
  and no TTFB for either. It is not comparable to a real-call trace. Only audio mode produces a
  trace with the same span set as a phone call.
- **This repo does not trace today.** `pipecat-bot/bot.py` calls
  `configure_pipecat()` (LangSmith) but constructs
  `PipelineWorker(pipeline, name="assistant", params=PipelineParams(enable_metrics=True, enable_usage_metrics=True))`
  — **without `enable_tracing=True`**. LangSmith's own docstring says so explicitly: *"Enable tracing
  on the pipeline itself with `PipelineTask(..., enable_tracing=True, enable_turn_tracking=True,
  params=PipelineParams(enable_metrics=True))`"*
  (`langsmith/integrations/pipecat/__init__.py`). Until that flag is set, neither real calls nor
  eval runs emit spans.
- **`configure_pipecat()` installs the provider with `exporter=None`** and wraps LangSmith's own
  exporter in a `PipecatLangSmithSpanProcessor`, so `opentelemetry-exporter-otlp` in `pyproject.toml`
  is not on that path (it is still the dependency that pulls in `opentelemetry-sdk`).

**The tagging hook.** `langsmith.integrations.pipecat.set_thread_id(thread_id)` — called once per
conversation, inside that conversation's asyncio task — writes
`langsmith.metadata.thread_id` onto every span in the trace and is stored in a `ContextVar`, so
concurrent conversations stay separated. Pipecat's own equivalents are
`PipelineWorker(conversation_id=..., additional_span_attributes={...})`. Either is the place to
stamp a scenario name onto an eval run's trace. Nothing in Pipecat Evals does this for you: **the
harness has no knowledge of tracing, and the bot has no knowledge of the scenario name.** Passing
the scenario name into the bot process would have to go through `--runner-body` (a JSON file the
suite manifest can supply per bot entry) or an environment variable in the `spawn:` template.

So: an eval run *does* produce a trace worth keeping — but only once the flag is on, only with the
span set the mode allows, and only correlated to its scenario if we do the stamping.

---

## 7. What comes back from a run, machine-readably

`EvalResult` ([`evals/harness.py` L205-233](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/harness.py)):

| Field | Content |
| --- | --- |
| `passed` | every assertion passed |
| `failures` | `EvalAssertionFailure(turn_index, expectation_index, event_name, reason, kind)` |
| `turns` | one `EvalTurnResult(turn_index, status, failures, duration_ms)`; `status ∈ {passed, failed, not_run}` |
| `duration_ms` | wall clock for the run |
| `events_seen` | every semantic event observed — **dicts with no timestamps** |
| `debug_log` | the harness's timestamped decision trace (`{seconds:8.3f}  [tN]  …`), written to `<scenario>.eval.log` |
| `skipped` | set with a reason when the scenario was not run; neither pass nor fail |

`kind` is the stable grouping key across runs — `timeout`, `judge_no`, `text_mismatch`,
`missing_function_call`, `handshake_timeout`, `harness_error`, … — because `reason` is free text and
often judge prose.

Library entry points, for a harness built *around* this
([library](https://docs.pipecat.ai/pipecat/evals/library.md)):

```python
from pipecat.evals.harness import EvalSession
from pipecat.evals.scenario import EvalScenario

scenario = EvalScenario.load("scenarios/booking.yaml")
result = await EvalSession.from_scenario(scenario, "ws://localhost:7860").run()

scored = [t for t in result.turns if t.status != "not_run"]  # not_run must not inflate a rate
```

`EvalManifest` / `EvalSuite` are the same for whole suites; each run is mutated in place
(`status`, `result`, `error`, `duration_ms`) so a live display can render from `suite.runs`.

**`--repeat N` / `repeat:`** runs each (bot, scenario) pair N times and reports a **pass rate**
instead of a verdict. Note the documented warning: **a repeated sweep always exits `0`** — a pass
rate is not a pass, so the threshold is ours to compute from `results.jsonl`. Leave `repeat` unset
for anything meant to gate.

---

## 8. The gaps — what a harness must build *around* Pipecat Evals

### 8.1 Latency numbers

`within_ms` is a **pass/fail budget, not a measurement**. Three specific limits:

1. **No number is reported.** A passing expectation yields no timing; `events_seen` entries carry no
   timestamps. Only `EvalTurnResult.duration_ms` (whole turn) and `EvalResult.duration_ms` (whole
   run) are structured. Per-event timings exist **only as text** in `debug_log` / `<scenario>.eval.log`
   (`{seconds:8.3f}` — millisecond resolution), which would have to be parsed.
2. **The anchor is wrong for TTFB.** All of a turn's expectations share one deadline anchored at the
   **send**, and for audio turns that is when the utterance was *sent*, not when it finished playing
   out of the virtual mic. So `within_ms` on the first `response` measures
   *(utterance duration + endpointing + STT + LLM + TTS)*, not time-to-first-audio after the Callee
   stopped speaking. It is also cumulative: time spent matching earlier expectations counts against
   later ones.
3. **It measures the harness's view, not the wire.** No jitter, no packet loss, no carrier path.

**What to use instead**: Pipecat's own OTel spans carry `metrics.ttfb` per service and
`turn.duration_seconds` per turn — which is the source the eval run should be read from for latency,
not the assertions. That requires §6's tracing flag. Keep `within_ms` as a coarse "did it stall"
guard (and note the docs' warning: with `stop_on_failure: false`, give every turn an explicit
`within_ms` or a silent bot burns 60 s per remaining turn).

### 8.2 WER, and Czech on narrowband

Pipecat Evals **has no WER metric at all** — no reference-transcript comparison, no alignment, no
error-rate field anywhere in `EvalResult`. `text_contains` and `eval:` are the only text checks, and
`eval:` is explicitly instructed to *forgive* transcription errors, which is the opposite of what a
WER measurement needs.

Narrowband specifically:

- The harness can synthesize at a chosen `user.speech.sample_rate`, but there is **no telephony
  codec emulation** — no G.711 μ-law/A-law round trip, no 8 kHz band-limiting filter, no packet loss
  or jitter. Setting `sample_rate: 8000` is a sample-rate change, not a phone line.
- The judge transcriber resamples everything to 16 kHz regardless.
- Czech has no built-in TTS voice: Kokoro's bundled voices do not cover it, so audio-mode Czech
  needs `cartesia` with a Czech-capable voice, or the `factory:` escape hatch to another HTTP/local
  TTS. Whichever is chosen must not be WebSocket-streaming.
- Czech transcription on the harness side needs `whisper` with a multilingual model
  (`moonshine`'s non-English models return empty transcripts, which read as "the bot said nothing").

**What has to be built**: the `record: true` / `-a` WAV of an audio-mode run is a real artifact and
the natural input to an out-of-band WER pass, and the `factory:` hook is the place to insert a
narrowband-degrading TTS wrapper. Both are outside the assertion vocabulary.
[09 — Czech narrowband sanity check](../../.scratch/v1-end-to-end/issues/09-czech-narrowband-sanity-check.md)
is where the baseline comes from either way.

### 8.3 A reactive Callee

Covered in §5: impossible in YAML, and no supported extension point in the library. Anything where
the Callee's *words* depend on the agent's behaviour is outside the framework.

### 8.4 The Outcome, and therefore task success

This is the sharpest gap, and it compounds the note already on
[10](../../.scratch/v1-end-to-end/issues/10-what-we-measure.md).

Pipecat Evals asserts on **RTVI events the bot emits during the conversation**. Our **Outcome**
(Disposition + Result + summary) is returned to the Client over A2A *after* the call. There is no
event for it and no assertion that can reach it. Combined with the standing decision from
[ADR 0001](../adr/0001-brief-and-outcome-contract.md) — no per-Constraint verdict field, so nothing
mechanically reports whether a non-negotiable Constraint held — this means:

- **Task success cannot be judged inside a scenario** unless the Outcome is made observable
  in-band. Two mechanisms exist that *could* carry it, both requiring bot-side design:
  a **function call** the bot makes to finalize the Outcome (assertable with `function_call` +
  `args` subset matching — deterministic, no judge), or an RTVI server message (no eval event maps
  to it).
- Otherwise the summary must be judged by a second, out-of-band pass over the returned Outcome —
  which is a LangSmith-side evaluation, not a Pipecat scenario.

The `naturalness.yaml` `end_call` pattern shows the first shape working in practice.

### 8.5 Smaller ones

- **No scores, only booleans.** Any weighted scorecard, per-criterion rating, or "N of M" threshold
  is computed by us from `results.jsonl` / `EvalResult.turns`.
- **No audio-signal quality metrics** — TTS loops, clipping, dropouts, timbre drift. The lifecycle
  doc names these as out of scope and points at platforms.
- **The eval transport is not the deployed path.** Explicitly listed as out of scope in
  [lifecycle](https://docs.pipecat.ai/pipecat/evals/lifecycle.md): "the local eval transport isn't a
  real WebSocket, Pipecat Cloud, SIP, or telephony path". Nothing here exercises a trunk.
- **State bleed between scenarios.** With `pipecat eval run a.yaml b.yaml`, one bot process serves
  all of them and `on_client_disconnected` is suppressed. LLM context is cleared per scenario only
  if `context:` is set; **application state is the bot's problem** — the harness cannot see it. Both
  upstream manifests use `pipecat eval suite` precisely to get one fresh bot per scenario.

---

## 9. Concrete deltas this repo needs before a first scenario runs

Facts about the current tree, not a plan.

| Gap | Where | Detail |
| --- | --- | --- |
| No `"eval"` transport entry | `pipecat-bot/bot.py` L40-45 | `transport_params` has only `"webrtc"`. `-t eval` would raise from `_get_transport_params`. Needs `EvalTransportParams`. |
| CLI not installed | `pyproject.toml` L14 | `pipecat-ai[cartesia,deepgram,openrouter,runner,silero,webrtc]` — no `cli` extra, so there is no `pipecat eval` command. Either `uv tool install "pipecat-ai[cli]"` or add the extra and use `uv run pipecat eval`. |
| Tracing off | `pipecat-bot/bot.py` L94-98 | `PipelineWorker(...)` without `enable_tracing=True` / `enable_turn_tracking=True`. `configure_pipecat()` alone emits nothing. |
| No judge available | — | Default judge is Ollama + `gemma4:12b` (`ollama pull gemma4:12b`); alternative is `judge.eval.service: openai` with `OPENAI_API_KEY`. Neither is present today. |
| Audio mode unavailable | `pyproject.toml` | Needs `kokoro` and `whisper` (or `moonshine`) extras for the harness's own TTS/STT. |
| Disconnect handler cancels the runner | `pipecat-bot/bot.py` L104-107 | Harmless by default (suppressed), but any scenario with `trigger_disconnect: true` ends the bot process. |

A minimal first scenario against this bot, once the transport entry exists:

```yaml
name: disclosure_first
turns:
  # Observation-only: the Disclosure must be the bot's opening move.
  - expect:
      - event: response
        eval: >-
          states plainly that this is an AI calling on behalf of a named person.
          It does not ask a question before saying so.
```

---

## 10. Sources

Documentation (docs.pipecat.ai, retrieved via the `pipecat-context-hub` MCP, index dated 2026-09-05):

- [`/pipecat/evals/overview.md`](https://docs.pipecat.ai/pipecat/evals/overview.md)
- [`/pipecat/evals/quickstart.md`](https://docs.pipecat.ai/pipecat/evals/quickstart.md)
- [`/pipecat/evals/scenarios.md`](https://docs.pipecat.ai/pipecat/evals/scenarios.md)
- [`/pipecat/evals/library.md`](https://docs.pipecat.ai/pipecat/evals/library.md)
- [`/pipecat/evals/suites.md`](https://docs.pipecat.ai/pipecat/evals/suites.md)
- [`/pipecat/evals/lifecycle.md`](https://docs.pipecat.ai/pipecat/evals/lifecycle.md)
- [`/api-reference/server/utilities/opentelemetry.md`](https://docs.pipecat.ai/api-reference/server/utilities/opentelemetry.md)

Framework source, `pipecat-ai/pipecat` at tag **`v1.8.1`**:

- [`src/pipecat/evals/scenario.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/scenario.py) — module docstring is the authoritative field list; `EvalExpectation` L272-306
- [`src/pipecat/evals/harness.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/harness.py) — `EvalResult` L205-233, run loop L559-580, turn send L1064-1085, budget anchoring L1088-1097
- [`src/pipecat/evals/judge.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/judge.py) — `JUDGE_SYSTEM_INSTRUCTION`, `JudgeVerdict`
- [`src/pipecat/evals/transport.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/transport.py) — query flags, virtual mic, `EvalTransportParams` L206
- [`src/pipecat/evals/services.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/services.py) — service/judge defaults, language coercion
- [`src/pipecat/evals/speech.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/speech.py) / [`transcribe.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/evals/transcribe.py) — sample rates, caching, 16 kHz resample
- [`src/pipecat/runner/utils.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/runner/utils.py) L716-740 · [`src/pipecat/runner/types.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/runner/types.py) L252-266 — transport selection
- [`src/pipecat/pipeline/worker.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/pipeline/worker.py) L296, L485-513 — `enable_rtvi` default
- [`src/pipecat/cli/commands/eval.py`](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/cli/commands/eval.py) — CLI flags for `run` and `suite`

Example scenarios, `pipecat-ai/pipecat-examples` at commit `87ede779`:

- [`phonellm/server/evals/scenarios/naturalness.yaml`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede7793edc6b9b49797dc8d3bc6cb51f6f9252/phonellm/server/evals/scenarios/naturalness.yaml)
- [`phonellm/server/evals/scenarios/reservations.yaml`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede7793edc6b9b49797dc8d3bc6cb51f6f9252/phonellm/server/evals/scenarios/reservations.yaml)
- [`phonellm/server/evals/scenarios/phonellm_smoke.yaml`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede7793edc6b9b49797dc8d3bc6cb51f6f9252/phonellm/server/evals/scenarios/phonellm_smoke.yaml)
- [`phonellm/server/evals/manifest.yaml`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede7793edc6b9b49797dc8d3bc6cb51f6f9252/phonellm/server/evals/manifest.yaml)
- [`ivr-navigation/evals/scenarios/human_answers.yaml`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede7793edc6b9b49797dc8d3bc6cb51f6f9252/ivr-navigation/evals/scenarios/human_answers.yaml)
- [`ivr-navigation/evals/scenarios/pharmacy_ivr.yaml`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede7793edc6b9b49797dc8d3bc6cb51f6f9252/ivr-navigation/evals/scenarios/pharmacy_ivr.yaml)
- [`ivr-navigation/evals/manifest.yaml`](https://github.com/pipecat-ai/pipecat-examples/blob/87ede7793edc6b9b49797dc8d3bc6cb51f6f9252/ivr-navigation/evals/manifest.yaml)

LangSmith integration, installed `langsmith` 0.12.x:

- `langsmith/integrations/pipecat/__init__.py` — `configure_pipecat()`, and its explicit requirement that the pipeline set `enable_tracing=True`
- `langsmith/_internal/voice/__init__.py` — `set_thread_id()` (ContextVar-scoped, applied to every span in the trace)
