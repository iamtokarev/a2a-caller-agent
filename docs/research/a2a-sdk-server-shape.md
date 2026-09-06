# The a2a-sdk server shape

Research for [issue 07](../../.scratch/v1-end-to-end/issues/07-a2a-sdk-server-shape.md).

**Everything below describes `a2a-sdk` 1.1.2** (released 2026-07-22, latest on PyPI as of 2026-09-06),
speaking **A2A protocol v1.0**. Version history: 1.0.0 shipped 2026-04-20, 1.0.1/1.0.2/1.0.3 through
May, 1.1.0 on 2026-05-29, 1.1.1 on 2026-07-16, 1.1.2 on 2026-07-22.
([PyPI JSON API](https://pypi.org/pypi/a2a-sdk/json))

Method: claims are taken from the 1.1.2 sdist source
(`a2a_sdk-1.1.2.tar.gz`), from the [A2A v1.0 specification](https://a2a-protocol.org/latest/specification/),
and from four executable probes run against a real server on this machine using
`a2a-sdk[fastapi]==1.1.2`. Where a probe established a fact, it says **[probe]**. Where the SDK
deviates from the spec, it says so.

> **Health warning on `docs/initial-research.md`.** That document's A2A section was written against
> the v0.3-era SDK and is wrong in specifics: there is no `A2AStarletteApplication`, no `DataPart`,
> no `AgentCard(url=...)`, and `TaskState` values are now `SCREAMING_SNAKE_CASE`. Its high-level
> judgement (Task primitive fits a phone call; `input-required` carries the Escalation) survives.
> Corrections are tabulated in [§10](#10-corrections-to-docsinitial-researchmd).

---

## 0. TL;DR — the load-bearing finding

**The SDK cannot wake a running executor.** `AgentExecutor.execute()` must *return* to yield control
at `input-required`; the Client's answer arrives as a **fresh `execute()` call** on the same task.
While `execute()` is still running, an incoming answer sits in a private queue and is not visible
to it by any supported means — proven by probe: an answer sent 3 s into a 12 s block was not
delivered until the block ended at 13.6 s ([§3.3](#33-proof-a-running-executor-cannot-be-woken)).

That is **not** fatal for a stalled phone call, because the live call does not have to live inside
the `execute()` stack frame. A call session object created in `execute()` **survives the return and
is still running when the second `execute()` arrives** — also proven by probe. So the shape is:

```
execute() #1   dial, talk, hit a Constraint you can't decide
               -> park the Pipecat session in a registry keyed by task_id
               -> emit input-required (the Escalation question)
               -> RETURN.  Phone line stays up; SSE stream for request #1 closes.
Client answers with the same task_id
execute() #2   look up the still-live session, inject the answer, keep talking
               -> emit the Outcome artifact -> completed -> RETURN.
```

The one genuine loss: **between the two calls the executor has no sanctioned way to emit events**,
so "the Callee hung up while we were waiting" cannot be reported until the Client answers. There is
an unsanctioned way that does work ([§3.6](#36-if-the-client-never-answers)); it is a real decision,
not a detail, and it is laid out with its trade-offs there.

---

## 1. Install and imports

`a2a-sdk` is not yet in `pyproject.toml`. For this project:

```toml
dependencies = [
  "a2a-sdk[fastapi]==1.1.2",   # pulls starlette + sse-starlette + fastapi
]
```

Extras that exist: `http-server` (starlette + sse-starlette), `fastapi`, `grpc`, `sqlite`,
`postgresql`, `mysql`, `sql`, `encryption`, `signing`, `telemetry`, `db-cli`, `all`.
Core deps are `httpx`, `pydantic`, `protobuf>=5.29.5,<7`, `google-api-core`, `json-rpc`,
`googleapis-common-protos`, `packaging`, and `culsans` on Python < 3.13. Requires Python >= 3.10.
(`a2a_sdk-1.1.2/pyproject.toml`)

**Pin exactly.** The map already records the standing decision to pin; 1.0.0 was a breaking rewrite
four months ago and the SDK still ships `_v2` modules alongside legacy ones.

### The types are protobuf, not Pydantic

This is the single biggest ergonomic change from the v0.3 SDK that `initial-research.md` assumes.
`a2a.types` re-exports generated protobuf messages from `a2a.types.a2a_pb2`. Consequences you will
feel on every line:

- Enum members are `TaskState.TASK_STATE_WORKING`, not `TaskState.working`.
- Optional submessages use `CopyFrom` / `HasField`, not assignment.
- `metadata` fields are `google.protobuf.Struct`; `Part.data` is a `google.protobuf.Value`.
- To go dict → proto use `google.protobuf.json_format.ParseDict`, and proto → dict `MessageToDict`.
  The SDK wraps the common cases in `a2a.helpers` (see [§6](#6-artifacts-and-structured-results)).

---

## 2. AgentCard: shape and serving

### 2.1 Required fields

Per the spec (§4.4.1), **required**: `name`, `description`, `supportedInterfaces`, `version`,
`capabilities`, `defaultInputModes`, `defaultOutputModes`, `skills`. Optional: `provider`,
`documentationUrl`, `securitySchemes`, `securityRequirements`, `signatures`, `iconUrl`.

**`AgentCard.url` no longer exists.** It was replaced by `supported_interfaces`, an *ordered* list of
`AgentInterface(url, protocol_binding, protocol_version, tenant)` — "the first entry is preferred"
(spec §4.4.1). `protocol_binding` is one of `JSONRPC`, `HTTP+JSON`, `GRPC`
(`a2a.utils.constants.TransportProtocol`).

`AgentSkill` fields: `id`, `name`, `description`, `tags`, `examples`, `input_modes`, `output_modes`,
`security_requirements`. Note `examples` moved from the card onto the skill in v1.0.

`AgentCapabilities` fields: `streaming`, `push_notifications`, `extensions`, `extended_agent_card`.
`input_modes`/`output_modes` were removed from capabilities — use the card-level defaults or the
per-skill overrides.

These two flags are **enforced**, not decorative. `DefaultRequestHandlerV2` decorates
`on_message_send_stream` and `on_subscribe_to_task` with a check on
`capabilities.streaming`, and every push-notification-config method with a check on
`capabilities.push_notifications`, raising `PushNotificationNotSupportedError` if false
(`default_request_handler_v2.py`). Declare both `True`.

### 2.2 A card for this agent

```python
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill

PUBLIC_URL = "https://<tunnel-host>"  # laptop + tunnel, per the map

AGENT_CARD = AgentCard(
    name="Voice Call Agent",
    description=(
        "Places one outbound phone call on a Principal's behalf, opens with an AI disclosure, "
        "holds the conversation in Czech or English, and returns a structured Outcome."
    ),
    version="0.1.0",
    provider=AgentProvider(organization="…", url="https://…"),
    capabilities=AgentCapabilities(streaming=True, push_notifications=True),
    default_input_modes=["application/json", "text/plain"],
    default_output_modes=["application/json", "text/plain"],
    skills=[
        AgentSkill(
            id="place_call",
            name="Place a phone call",
            description=(
                "Given a Brief (Objective, Callee, Constraints, Principal, language, useful_until), "
                "place one outbound call and return an Outcome (Disposition + Result + summary). "
                "May pause in input-required to pose an Escalation."
            ),
            tags=["voice", "telephony", "outbound", "booking"],
            examples=['{"objective": "Book a table for 4 on Saturday at 19:00", "callee": {...}}'],
            input_modes=["application/json"],
            output_modes=["application/json"],
        )
    ],
    supported_interfaces=[
        AgentInterface(
            protocol_binding="JSONRPC", protocol_version="1.0", url=f"{PUBLIC_URL}/a2a/jsonrpc"
        ),
    ],
)
```

The `url` in each `AgentInterface` must be the **tunnel-public** URL, not `127.0.0.1` — it is what
the Client dials. This is the one piece of config that has to change when the tunnel does.

### 2.3 Where the card is served

Well-known path `\/.well-known/agent-card.json`, registered with IANA in spec §14.3 and hard-coded as
`a2a.utils.constants.AGENT_CARD_WELL_KNOWN_PATH`. The SDK gives you a route factory:

```python
create_agent_card_routes(agent_card, card_modifier=None,
                         card_url="/.well-known/agent-card.json") -> list[Route]
```

**The wrapper application classes are gone.** `A2AStarletteApplication`, `A2AFastApiApplication` and
`A2ARESTFastApiApplication` were removed in v1.0; you compose Starlette routes yourself
(migration guide §5, shipped in the sdist at `docs/migrations/v1_0/README.md`).

```python
from fastapi import FastAPI
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)

app = FastAPI()
add_a2a_routes_to_fastapi(
    app,
    agent_card_routes=create_agent_card_routes(agent_card=AGENT_CARD),
    jsonrpc_routes=create_jsonrpc_routes(request_handler=handler, rpc_url="/a2a/jsonrpc"),
)
```

Plain Starlette works too — `create_*_routes` return `Route` objects you pass to
`Starlette(routes=[...])`. FastAPI buys you `/docs` visibility for the A2A routes (a 1.1.0 feature)
and lets you hang the telephony webhooks off the same app.

---

## 3. The `input-required` round-trip

This is the mechanism [issue 03 (escalation policy)](../../.scratch/v1-end-to-end/issues/03-escalation-policy.md)
depends on, so it is documented in more depth than the rest.

### 3.1 How the machinery actually works

`DefaultRequestHandler` is an alias for `DefaultRequestHandlerV2`
(`a2a/server/request_handlers/__init__.py`). It owns an `ActiveTaskRegistry`: a
`dict[task_id, ActiveTask]`. Each `ActiveTask` spawns exactly two long-lived `asyncio.Task`s
(`active_task.py`):

- a **producer** running a `while True` loop that pulls `(RequestContext, request_id)` off a private
  `_request_queue` and `await`s `AgentExecutor.execute(context, event_queue)` for each one, in order;
- a **consumer** that drains the event queue, persists state via `TaskManager`, fires push
  notifications, and fans events out to subscribers.

The producer acquires `_request_lock` before each `execute()`. It is released only when the consumer
sees the internal `_RequestCompleted` sentinel — which the producer enqueues **after `execute()`
returns**. So requests against one task are strictly serialised behind the current `execute()`.

`on_message_send_stream` calls `active_task.subscribe(request=request_context)`, which enqueues the
request and then yields events until it sees the `_RequestCompleted` matching *its own* request_id,
at which point the async generator returns and the SSE connection closes.

### 3.2 What that means for the round-trip

1. Executor emits `TaskStatusUpdateEvent(state=TASK_STATE_INPUT_REQUIRED)` carrying the Escalation
   question as the status `message`, then **returns**.
2. The SSE stream opened by the Client's first `SendStreamingMessage` **closes** (the SDK ties stream
   lifetime to the request, not to the task — CHANGELOG 1.0.0: *"rely on agent executor
   implementation for stream termination"*).
3. `INPUT_REQUIRED` is *not* terminal, so the `ActiveTask` stays in the registry with its producer
   parked on `_request_queue.get()`, and `_maybe_cleanup` does not fire (it needs `_is_finished`,
   which is set only on a terminal state).
4. The Client sends a new message with the **same `task_id`** (and matching `context_id`).
   `_setup_active_task` finds the existing `ActiveTask` via `get_or_create`, and `enqueue_request`
   puts it on the same queue. The parked producer wakes and calls `execute()` again.
5. The second `execute()` receives a **new `RequestContext`** whose `message` is the answer and whose
   `current_task` is the stored `Task` with `status.state == TASK_STATE_INPUT_REQUIRED` and the full
   `history`. **[probe]**

**[probe] Probe 1 output** (executor keeps a `LiveCall` object outside the stack frame):

```
execute() ENTER task=30517b06 existing_call=False state=None msg='Book a table for 4 at 19:00'
  <- Task TASK_STATE_SUBMITTED
  <- status TASK_STATE_WORKING: 'dialling'
  -> escalating; call alive ticks=2
  execute() RETURN at input-required
  <- artifact 'progress' last_chunk=False
  <- status TASK_STATE_INPUT_REQUIRED: 'Only 20:30 free. Accept?'
--- stream 1 CLOSED (executor returned at input-required) ---
get_task -> TASK_STATE_INPUT_REQUIRED artifacts=['progress']
--- stream 2: the answer, same task_id ---
execute() ENTER task=30517b06 existing_call=True state=TASK_STATE_INPUT_REQUIRED msg='accept'
  RESUMED. same LiveCall obj=30517b06 still_running=True ticks=7
  <- status TASK_STATE_WORKING: "relaying 'accept' to callee"
  <- artifact 'outcome' data=[{'result':'achieved','summary':"Booked 20:30 after client said 'accept'",'disposition':'connected'}]
  <- status TASK_STATE_COMPLETED
```

`ticks` 2 → 7 is the point: **the background "call" kept running across the gap**, and execute #2 got
the identical object back. That is the stall-on-the-line behaviour the map calls for.

### 3.3 Proof a running executor cannot be woken

Probe 3 emits `input-required` and then deliberately stays inside `execute()` for 12 s. The answer is
sent concurrently at t=4.6 s:

```
P3 [  1.6s] execute ENTER msg='go' state=None
P3 [  1.6s]   emitted input-required; now STAYING INSIDE execute() for 12s (simulating held phone line)
P3 [  4.6s] get_task while executor still blocked -> TASK_STATE_INPUT_REQUIRED
P3 [  4.6s] NOW sending the answer CONCURRENTLY (executor still inside execute)...
P3 [ 10.6s] RESULT: after 6s the answer has NOT reached the executor (still blocked)
P3 [ 13.6s]   block finished; completing
P3 [ 13.6s] execute ENTER msg='answer' state=TASK_STATE_COMPLETED
```

The answer waited the full 12 s. **Do not design around blocking inside `execute()` for an answer.**

Note the last line's hazard: the queued request was *still executed* after the task had already gone
terminal, so execute #2 ran with `state=TASK_STATE_COMPLETED`. An executor must therefore check
`context.current_task.status.state` and no-op on a terminal task rather than assume it is only ever
called on a live one.

### 3.4 The executor contract, verbatim

From the `AgentExecutor.execute` docstring (`agent_executor.py`) — this is the SDK's own statement of
the rule, worth quoting because it settles the question:

> `TASK_STATE_INPUT_REQUIRED`: The executor publishes a `TaskStatusUpdateEvent` with
> `TaskState.TASK_STATE_INPUT_REQUIRED` and returns to yield control. The request will resume once
> user input is provided.

and

> **Post-Completion**: Once `execute()` completes (returns or raises), the executor must not access
> the `context` or `event_queue` anymore.

The docstring also documents an **out-of-band `AUTH_REQUIRED`** variant where the agent *does* stay
inside `execute()` and waits. There is no equivalent sanctioned variant for `INPUT_REQUIRED`,
because auth completes via a third party while input must come back through A2A.

### 3.5 Timeout and expiry semantics

**There are none in the SDK.** A grep of `a2a/server/` for `timeout|expir|ttl` finds only the
event-consumer's 0.5 s internal poll and queue-shutdown helpers — nothing that ages out an
`input-required` task. The spec likewise sets no deadline; it only says agents "MAY implement context
expiration or cleanup policies and SHOULD document any such policies" (§3.4.1). A task can sit in
`input-required` indefinitely.

**So the Escalation deadline is entirely ours to build**, and it is a real requirement here: a phone
line is held open while we wait, and the Brief's `useful_until` (ADR 0001) is the only clock the
Client gave us. This is input to issue 03, not something the protocol solves.

### 3.6 If the Client never answers

Because nothing re-enters `execute()`, a watchdog has no *sanctioned* way to end the task. Two
options; the choice belongs to the human.

**Option A — bounded stall, decide locally, report on resume (contract-clean).**
Give the stall a short deadline (seconds, not minutes). On expiry the Pipecat session ends the call
politely per the Call-back policy and *buffers* the Outcome. The buffered Outcome is emitted by the
next `execute()`, whenever the Client answers. Cost: the task sits in `input-required` and the Client
sees nothing until it replies. If the Client never replies, the task never resolves — but the phone
line is down and no real-world harm continues.

**Option B — retain the `event_queue` and emit from a watchdog (works, violates the contract).**

**[probe] Probe 4 confirms this works in 1.1.2.** A task created inside `execute()` that keeps the
`TaskUpdater` and fires 4 s after `execute()` returned:

```
P4 [  1.6s]   execute() RETURN at input-required, watchdog armed for 4s
P4 [  1.6s]   stream1 CLOSED at input-required
P4 [  5.6s]   WATCHDOG fires (no answer from Client). Trying to emit on retained queue...
P4 [  5.6s]   WATCHDOG: emission SUCCEEDED after execute() had returned
P4 [  5.6s]   SUBSCRIBER <- artifact_update
P4 [  5.6s]   SUBSCRIBER <- status_update
P4 [  7.6s] FINAL get_task -> TASK_STATE_FAILED artifacts=['outcome']
```

The artifact and the terminal state persisted, reached a `SubscribeToTask` subscriber, and would
reach a push webhook. It works because `_event_queue_agent` is owned by the `ActiveTask`, not by the
request, and the consumer is still draining it.

Trade-off, stated plainly: this is **explicitly forbidden by the documented contract** and relies on
an implementation detail that the SDK is free to change in a minor release (it already renamed
`event_queue` → `event_queue_v2` and `DefaultRequestHandler` → `…V2` inside one major version). It
buys the ability to close out a task the Client abandoned, and to report "the Callee hung up while
we waited". If chosen, isolate it behind one function so a version bump breaks exactly one place,
and pin the SDK hard.

### 3.7 Posing the question and reading the answer

The Escalation from ADR 0001 is "a free-text question with an optional list of enumerated options".
That maps onto the status `message`, which is a full `Message` with multiple `Part`s — so the prose
question and the options can travel together:

```python
from a2a.helpers import new_data_part
from a2a.types import Part

await updater.requires_input(
    updater.new_agent_message(
        parts=[
            Part(text="They have nothing at 19:00, only 20:30. Do you want the later table?"),
            new_data_part(
                {"options": ["accept_2030", "decline", "try_another_day"]},
                media_type="application/json",
            ),
        ]
    )
)
```

The Client answers by sending a `Message` with the same `task_id`. Read it in execute #2 with
`context.get_user_input()` for the free-text reply, or `get_data_parts(context.message.parts)` for a
chosen option — supporting both is what ADR 0001's "choosing one or replying in free text" requires.

---

## 4. AgentExecutor: the execute / cancel contract

```python
class AgentExecutor(ABC):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None: ...
    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None: ...
```

### 4.1 Two legal streaming patterns, now enforced

v1.0 **hard-enforces** the spec's stream rules; v0.3 tolerated violations. Pick exactly one
(migration guide §4, `EventConsumer._handle_message_event` / `_handle_task_event`):

1. **Message-only** — enqueue exactly one `Message`, stop. No task, no tracking.
2. **Task lifecycle** — enqueue a `Task` **first**, then zero or more `TaskStatusUpdateEvent` /
   `TaskArtifactUpdateEvent` until terminal.

Each of these now raises `InvalidAgentResponseError` at runtime:

| Violation | Message |
|---|---|
| `Message` after a `Task` | *Received Message object in task mode…* |
| More than one `Message` | *Multiple Message objects received.* |
| Task/update event after a `Message` | *Received `<Type>` in message mode…* |
| `TaskStatusUpdateEvent` before the initial `Task` | *Agent should enqueue Task before `<Type>` event* |

A phone call is unambiguously pattern 2.

### 4.2 Other contract points from the docstring

- **Concurrency**: the framework guarantees `execute()` is never called concurrently for the same
  task (the `_request_lock`).
- **Exceptions**: an unhandled exception is caught by the framework and the task is persisted as
  `FAILED`. Verified in `_run_producer`'s `except Exception` branch.
- **`return_immediately`**: the framework holds the `send_message` response until the executor
  enqueues its first `Task` or `Message`. So enqueue the initial `Task` promptly — before dialling,
  not after the Callee picks up.

### 4.3 Cancellation

`ActiveTask.cancel()` does two things, in this order: `self._producer_task.cancel()` — which raises
`asyncio.CancelledError` **inside your running `execute()`** — and then `await
self._agent_executor.cancel(request_context, event_queue)`. **[probe]** Probe 2 §F:

```
P2:   cancel ENTER task=dc6c1d54
P2:   execute() saw CancelledError (this is where you hang up the phone)
P2:   cancel_task -> TASK_STATE_CANCELED
```

So there are two places to hang up the phone, and you want both:

```python
async def execute(self, context, event_queue):
    session = ...
    try:
        ...
    except asyncio.CancelledError:
        await session.hangup()  # the line must not stay open
        raise
```

```python
async def cancel(self, context, event_queue):
    session = CALL_SESSIONS.pop(context.task_id, None)
    if session:
        await session.hangup()
    await TaskUpdater(event_queue, context.task_id, context.context_id).cancel()
```

`cancel()` **must** emit the `CANCELED` status itself — the framework does not. `ActiveTask.cancel`
then blocks on `_is_finished.wait()`, so a `cancel()` that never emits a terminal state hangs the
caller.

### 4.4 Long-running work that outlives one request

Two independent facts, both probe-verified:

- The **`ActiveTask` and its producer outlive an individual request** and stay resident while the
  task is non-terminal.
- **Your own `asyncio.Task`s survive `execute()` returning** and are reachable from the next
  `execute()` via any registry you keep.

Concretely: keep `CALL_SESSIONS: dict[str, CallSession]` at module scope keyed by `task_id`, create
the Pipecat session in execute #1, and look it up in execute #2. Clear it in a `finally` on the
terminal path and in `cancel()`. Note the SDK keeps `ActiveTask` objects in memory for every
non-terminal task with no TTL, so an abandoned `input-required` task leaks an `ActiveTask` *and*
whatever you parked next to it — another reason to give the stall a deadline.

---

## 5. Task states and legal transitions

Nine values (`a2a.types.a2a_pb2.TaskState`, numeric values confirmed by introspection):

| Value | # | Class |
|---|---|---|
| `TASK_STATE_UNSPECIFIED` | 0 | unknown |
| `TASK_STATE_SUBMITTED` | 1 | active |
| `TASK_STATE_WORKING` | 2 | active |
| `TASK_STATE_COMPLETED` | 3 | **terminal** |
| `TASK_STATE_FAILED` | 4 | **terminal** |
| `TASK_STATE_CANCELED` | 5 | **terminal** |
| `TASK_STATE_INPUT_REQUIRED` | 6 | *interrupted* |
| `TASK_STATE_REJECTED` | 7 | **terminal** |
| `TASK_STATE_AUTH_REQUIRED` | 8 | *interrupted* |

The SDK codifies the two classes in `active_task.py`:

```python
TERMINAL_TASK_STATES = {COMPLETED, CANCELED, FAILED, REJECTED}
INTERRUPTED_TASK_STATES = {AUTH_REQUIRED, INPUT_REQUIRED}
```

### What is and isn't enforced

**There is no state-transition table.** Neither the spec nor the SDK enumerates legal edges. What is
actually enforced:

- `TaskUpdater` raises `RuntimeError("Task … is already in a terminal state")` if you call
  `update_status` twice past a terminal state — but only within one `TaskUpdater` instance, and each
  `execute()` typically makes a fresh one.
- A `TaskStatusUpdateEvent` before the initial `Task` raises `InvalidAgentResponseError`.
- Sending a message to, or subscribing to, a **terminal** task is refused. **[probe]** The SDK raises
  `InvalidParamsError` / JSON-RPC `-32602` *"Task … is already completed."* — note the spec (§3.1.1,
  §3.1.6) says this should be `UnsupportedOperationError`. **SDK deviation; do not match on the error
  type across implementations.**

Everything else is on the agent. The transitions this project will use:

```
submitted ──► working ──────────────► completed        (Objective achieved, or Callee declined)
                 │  ▲                 
                 │  └── working  ◄── (Client's answer, execute #2)
                 └────► input-required                 (Escalation posed, execute #1 returns)
   any active ──► failed                               (dial_failed, crash, escalation timeout)
   any active ──► canceled                             (Client called CancelTask)
```

`rejected` is available for a Brief the agent refuses up front (bad E.164, `useful_until` already
past, unsupported language) — cheaper than `failed` and semantically right: "the agent has decided
to not perform the task" (spec §4.1.3). Worth considering for Brief validation.

Note the mapping tension worth handing to
[issue 14](../../.scratch/v1-end-to-end/issues/14-call-progress-onto-task-lifecycle.md): ADR 0001
says a Callee who answers and declines is a **successful Disposition with a negative Result**. That
must be `TASK_STATE_COMPLETED` with `result: not_achieved` in the artifact — *not* `failed`.
`failed` should be reserved for the call not happening (`dial_failed`) or the agent breaking.

### What closes a stream

Two different answers, and the difference matters:

- **Spec** (§3.1.2): a task-lifecycle stream "MUST close when the task reaches a terminal state".
- **SDK 1.1.2**: a `SendStreamingMessage` stream closes when **that request's `execute()` returns** —
  which at `input-required` is *before* any terminal state. **[probe]** Confirmed in probes 1–4.
  A `SubscribeToTask` stream does follow the spec: it runs until terminal. **[probe]**

So a Client that wants one continuous view across an Escalation should use `SubscribeToTask` or push
notifications, not the first `SendStreamingMessage` stream. This is a **deviation to design around**,
and it directly shapes what [issue 16](../../.scratch/v1-end-to-end/issues/16-thin-a2a-test-client.md)
must write.

---

## 6. Artifacts and structured results

### 6.1 `DataPart` is gone

In v1.0 the wrapper types `TextPart` / `FilePart` / `DataPart` were **removed**. `Part` is a single
message with a `oneof content`:

| Content | v0.3 | v1.0 |
|---|---|---|
| Text | `Part(TextPart(text=...))` | `Part(text=...)` |
| File bytes | `Part(FilePart(file=FileWithBytes(...)))` | `Part(raw=<bytes>)` |
| File URI | `Part(FilePart(file=FileWithUri(...)))` | `Part(url=...)` |
| **Structured data** | `Part(DataPart(data={...}))` | **`Part(data=<google.protobuf.Value>)`** |

Plus `metadata`, `filename`, `media_type` alongside the oneof. Use the helper rather than hand-rolling
the `Value`:

```python
from a2a.helpers import new_data_part, new_data_artifact, get_data_parts

part = new_data_part({"disposition": "connected", ...}, media_type="application/json")
data = get_data_parts(artifact.parts)   # -> list[dict]
```

**[probe]** Verified round-trip: a dict goes in as `Part(data=...)`, serialises to
`{"data": {...}, "mediaType": "application/json"}` on the wire, and comes back as the same dict.

### 6.2 The Outcome as an artifact

```python
await updater.add_artifact(
    parts=[
        new_data_part(
            {
                "disposition": "connected",  # connected|voicemail|no_answer|wrong_number|dial_failed
                "result": "achieved",  # achieved|not_achieved|undetermined
                "summary": "Booked for 4 at 20:30 under the Principal's name.",
                "details": {"confirmed_time": "20:30", "reference": "…"},
            },
            media_type="application/json",
        )
    ],
    name="outcome",
    last_chunk=True,
)
await updater.complete()
```

Per ADR 0001 the Outcome carries **no transcript** — keeping a recording of an identifiable person off
the A2A boundary. Nothing in the protocol enforces that; it is our discipline.

### 6.3 Progressive emission

Yes. `TaskArtifactUpdateEvent` has `append: bool` and `last_chunk: bool`, and
`TaskUpdater.add_artifact` exposes both plus a stable `artifact_id`. Repeated events with the same
`artifact_id` and `append=True` accumulate into one artifact server-side
(`task_manager.append_artifact_to_task`). **[probe]** Probe 1 emitted a `progress` artifact with
`last_chunk=False` during the call and an `outcome` artifact at the end; `get_task` mid-call showed
`artifacts=['progress']`.

One gotcha from CHANGELOG 1.1.0: *"raise on `append=True` for unknown `artifact_id`"* — you must emit
the first chunk of an artifact **without** `append=True`.

For this project the sensible split is a running `progress` artifact (call-stage breadcrumbs) plus a
single `outcome` artifact emitted `last_chunk=True` at the end. Whether the Client wants progressive
breadcrumbs at all is a product call, not a protocol constraint.

---

## 7. Push notifications and `SubscribeToTask`

### 7.1 Wiring the server

Push is **opt-in** and off unless you pass both stores:

```python
import httpx
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)

push_configs = InMemoryPushNotificationConfigStore()
handler = DefaultRequestHandler(
    agent_executor=VoiceCallExecutor(),
    task_store=InMemoryTaskStore(),
    agent_card=AGENT_CARD,
    push_config_store=push_configs,
    push_sender=BasePushNotificationSender(httpx.AsyncClient(timeout=15), push_configs),
)
```

`DatabasePushNotificationConfigStore` is the persistent alternative.

### 7.2 What the Client registers

Either inline on the first message (one round trip — recommended, so no event can be missed):

```python
SendMessageRequest(
    message=...,
    configuration=SendMessageConfiguration(
        task_push_notification_config=TaskPushNotificationConfig(
            url="https://client.example/hooks/a2a",
            token="…",
        )
    ),
)
```

…or afterwards via `CreateTaskPushNotificationConfig` (`client.create_task_push_notification_config`).
`TaskPushNotificationConfig` fields: `url` (required), `id`, `task_id`, `token`, `authentication`
(`AuthenticationInfo(scheme, credentials)`), `tenant`.

### 7.3 What gets delivered

**[probe]** With the inline config, the webhook received a POST for the initial `Task` and for **every**
status update, including `INPUT_REQUIRED`:

```
WEBHOOK <- {"task": {"id": "...", "status": {"state": "TASK_STATE_SUBMITTED"}, ...}}
WEBHOOK <- {"statusUpdate": {... "status": {"state": "TASK_STATE_WORKING", ...}}}
WEBHOOK <- {"statusUpdate": {... "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {...}}}}
```

Body is a `StreamResponse` JSON object with exactly one of `task` / `message` / `statusUpdate` /
`artifactUpdate` (spec §4.3.3) — the same shape as a streaming event.

**Two SDK gaps worth knowing before you rely on this:**

1. `BasePushNotificationSender._dispatch_notification` sends the `token` as an
   **`X-A2A-Notification-Token`** header. It **ignores `TaskPushNotificationConfig.authentication`**
   entirely — the spec (§4.3.3) says the agent MUST send
   `Authorization: {scheme} {credentials}`. If the Client needs real auth on its webhook, that
   header must be added by subclassing the sender.
2. **No retries.** A failed POST is logged via `logger.exception` and returns `False`; nothing
   re-delivers. The spec permits this (retries are MAY) but requires at-least-once *attempt*. On a
   laptop-plus-tunnel deployment a dropped webhook is silently lost, so the Client should treat push
   as a hint and reconcile with `GetTask`.

### 7.4 Resuming a dropped stream

`SubscribeToTask` (`client.subscribe(SubscribeToTaskRequest(id=task_id))`) is the resume path.
Server side, `on_subscribe_to_task` looks the task up in the registry and calls
`active_task.subscribe(include_initial_task=True)` — so the subscriber **first receives the current
`Task` snapshot**, then live events. That closes the gap between disconnect and reconnect for state,
though *events emitted while disconnected are not replayed* — only the resulting task state is. There
is no event cursor or replay buffer in the SDK.

**[probe]** A subscriber attached to a task sitting in `input-required` saw
`['TASK_STATE_INPUT_REQUIRED', 'TASK_STATE_WORKING', 'TASK_STATE_COMPLETED']` and its stream ended at
terminal. Subscribing to an already-terminal task is refused (`-32602`).

Multiple concurrent streams per task are explicitly legal (spec §3.5.2): events broadcast to all,
same order, closing one does not affect others. The SDK implements this by tapping the subscriber
queue per subscription.

---

## 8. TaskStore for a single-machine v1

`TaskStore` is a four-method ABC: `save`, `get`, `list`, `delete`, each taking a `ServerCallContext`.

| | `InMemoryTaskStore` | `DatabaseTaskStore` |
|---|---|---|
| Extra deps | none | `a2a-sdk[sqlite]` (or `[postgresql]`/`[mysql]`) |
| Setup | `InMemoryTaskStore()` | build an `AsyncEngine`, then `await store.initialize()` |
| Survives restart | **no** | yes |
| Notes | nested dict keyed by owner then task_id; `use_copying=True` by default | `create_table=True` by default; `table_name` configurable; Alembic migrations shipped under `a2a/migrations` with an `a2a-db` CLI |

**The honest v1 assessment.** Persisting tasks does *not* buy resumability here, because the thing
that cannot be restored is the live Pipecat session and the phone leg — both die with the process.
After a restart, `get_or_create` on a stored `input-required` task will happily spin up a fresh
`ActiveTask` and call `execute()` with `current_task` loaded from the store, but `CALL_SESSIONS` is
empty and there is no call to resume. So a database mainly buys **post-hoc inspection**: the Client
can still `GetTask` and read the Outcome artifact after a crash or restart.

Two defensible positions, both consistent with the map's "laptop plus tunnel is the whole deployment
story":

- **`InMemoryTaskStore`** — simplest, zero deps, and honest about the fact that a restart loses the
  call anyway. Costs the Client its ability to fetch a completed Outcome after a restart.
- **`DatabaseTaskStore` on SQLite** — one extra extra and one `await store.initialize()`, and the
  Outcome survives. Also gives the eval harness
  ([issue 12](../../.scratch/v1-end-to-end/issues/12-build-the-eval-harness.md)) a durable record of
  every task to score against, which may matter more than the resumability it doesn't provide.

Either way the executor must handle "called with `current_task` set but no live session" — after a
restart that is exactly what it will see. Failing the task with `disposition: dial_failed` is the
safe response.

---

## 9. Minimum client code

For [issue 16](../../.scratch/v1-end-to-end/issues/16-thin-a2a-test-client.md). The v1.0 client is
built through `ClientFactory`; `Client.send_message` returns an `AsyncIterator[StreamResponse]` in
both streaming and non-streaming mode, so the same loop shape works either way.

```python
import asyncio, httpx
from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
from a2a.helpers import get_data_parts, get_message_text
from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    SendMessageConfiguration,
    TaskPushNotificationConfig,
    TaskState,
)

AGENT = "https://<tunnel-host>"


async def delegate_a_call(brief: dict) -> dict:
    async with httpx.AsyncClient(timeout=None) as http:
        card = await A2ACardResolver(http, AGENT).get_agent_card()
        client = ClientFactory(
            ClientConfig(
                httpx_client=http,
                streaming=True,
                supported_protocol_bindings=["JSONRPC"],
            )
        ).create(card)

        task_id = context_id = None
        outcome = None
        message = Message(
            message_id="brief-1",
            role=Role.ROLE_USER,
            parts=[new_data_part(brief, media_type="application/json")],
        )

        while True:
            state = None
            async for ev in client.send_message(SendMessageRequest(message=message)):
                which = ev.WhichOneof("payload")
                if which == "task":
                    task_id, context_id = ev.task.id, ev.task.context_id
                    state = ev.task.status.state
                elif which == "status_update":
                    state = ev.status_update.status.state
                    if state == TaskState.TASK_STATE_INPUT_REQUIRED:
                        question = ev.status_update.status.message  # the Escalation
                elif which == "artifact_update":
                    if ev.artifact_update.artifact.name == "outcome":
                        outcome = get_data_parts(ev.artifact_update.artifact.parts)[0]
            # The stream closed. Why?
            if state == TaskState.TASK_STATE_INPUT_REQUIRED:
                answer = await decide(
                    get_message_text(question), get_data_parts(question.parts)
                )  # human or LLM
                message = Message(
                    message_id=f"answer-{task_id}",
                    role=Role.ROLE_USER,
                    task_id=task_id,
                    context_id=context_id,  # BOTH, and they must match
                    parts=[Part(text=answer)],
                )
                continue  # a NEW streaming call carries the answer
            return outcome
```

Three things this client must get right, all of them consequences of findings above:

1. **The answer is a new `send_message`, not a write into the open stream.** There is no such thing
   as writing back into an SSE stream.
2. **`task_id` is mandatory on the answer**, and `context_id` must match the task's or the server
   rejects it (spec §3.4.3). Client-supplied `task_id` for a *new* task is not supported — the server
   generates it.
3. **The loop is driven by the state at stream close**, because the stream closes at
   `input-required` ([§5](#what-closes-a-stream)). A client that assumes "stream closed ⇒ task done"
   will hang up on its own Escalation.

To watch a call continuously across an Escalation instead, register a push webhook on the first
message and/or hold a `client.subscribe(SubscribeToTaskRequest(id=task_id))` alongside — that stream
does survive to terminal.

---

## 10. Corrections to `docs/initial-research.md`

| Claim there | Status |
|---|---|
| A2A v1.0 released 2026-04-09; Task lifecycle fits a phone call | **Holds.** SDK 1.0.0 followed on 2026-04-20. |
| `input-required` carries mid-call questions; client replies on the same task/context ID | **Holds**, with the crucial addition that the executor is *re-invoked*, not resumed in place. |
| "the voice agent transitions the task to `input-required` … and the call continues" | **Half-true and misleading.** The *call* continues only if you park the session outside `execute()`. The *executor* does not continue — it returns and is called again. |
| `A2AStarletteApplication` on Uvicorn | **Wrong.** Removed in v1.0; use `create_jsonrpc_routes` / `create_agent_card_routes` / `add_a2a_routes_to_fastapi`. |
| `AgentCard(name, url, version, capabilities, skills)` | **Wrong field.** `url` removed; use `supported_interfaces=[AgentInterface(...)]`. |
| `AgentCapabilities(streaming=True, pushNotifications=True)` | **Right idea, wrong case.** Python attr is `push_notifications`; JSON is `pushNotifications`. |
| `TaskStatusUpdateEvent` with a `DataPart` carrying JSON | **`DataPart` no longer exists.** Use `Part(data=...)` / `new_data_part`. |
| Lifecycle `submitted → working → input-required → completed/failed/canceled/rejected` | **Holds**, plus `auth_required` and `unspecified`. |
| "the streaming contract … emits events until a terminal state, at which point the stream closes" | **True of the spec and of `SubscribeToTask`; false of `SendStreamingMessage` in SDK 1.1.2**, which closes when `execute()` returns. |
| Push notifications via `CreateTaskPushNotificationConfig`; `SubscribeToTask` to resume | **Holds.** Note the SDK ignores `authentication` and does not retry. |
| Core methods `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask` + push config methods | **Holds.** |
| "pin your SDK versions" | **Emphatically holds.** |

---

## 11. Open questions this research does not settle

- **The Escalation deadline.** No protocol or SDK timer exists ([§3.5](#35-timeout-and-expiry-semantics));
  how long to hold the line, and what to do at expiry, is
  [issue 03](../../.scratch/v1-end-to-end/issues/03-escalation-policy.md)'s to decide.
- **Contract-clean vs. watchdog emission** ([§3.6](#36-if-the-client-never-answers)) — a genuine
  trade-off between spec-compliance and the ability to close out an abandoned task.
- **In-memory vs. SQLite TaskStore** ([§8](#8-taskstore-for-a-single-machine-v1)) — turns on whether
  the eval harness wants a durable task record.
- **Call-back as a new Task or the same one.** The map lists this as open in `PRODUCT-OVERVIEW.md`.
  The protocol supports both: a second Task within the same `context_id` (spec §3.4.3: "Clients MAY
  use `contextId` without `taskId` to start a new task within an existing conversation context"), or
  keeping one Task alive across both calls. Nothing here forces the choice.

## Sources

- `a2a-sdk` 1.1.2 sdist, read directly: `src/a2a/server/agent_execution/{agent_executor,active_task,active_task_registry,context}.py`,
  `src/a2a/server/request_handlers/default_request_handler_v2.py`,
  `src/a2a/server/tasks/{task_updater,task_store,inmemory_task_store,database_task_store,base_push_notification_sender,task_manager}.py`,
  `src/a2a/server/routes/agent_card_routes.py`, `src/a2a/utils/constants.py`,
  `src/a2a/helpers/proto_helpers.py`, `samples/hello_world_agent.py`, `CHANGELOG.md`,
  `docs/migrations/v1_0/README.md`, `pyproject.toml`.
- [A2A v1.0 specification](https://a2a-protocol.org/latest/specification/) — §3.1.1–3.1.6, §3.4,
  §3.5, §4.1.3, §4.2, §4.3, §4.4, §14.3.
- [a2a-sdk on PyPI](https://pypi.org/project/a2a-sdk/) for the version/date table.
- [a2a-protocol.org Python SDK API reference](https://a2a-protocol.org/latest/sdk/python/) via `ctx7`.
- Four executable probes against a live `a2a-sdk` 1.1.2 server (input-required round-trip; blocking
  executor; push + subscribe + cancel + terminal-task rejection; post-return watchdog emission).
  Not committed — they were scratch, and their output is quoted inline above.
