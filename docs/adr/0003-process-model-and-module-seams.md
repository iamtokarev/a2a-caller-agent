# Process model and module seams

Four modules: **`a2a/`** (the peer interface), **`call/`** (the conversation), **`telephony/`**
(transport configuration), and a fourth holding the ADR-0001 types — Brief, Outcome, Escalation —
that depends on nothing and that both `a2a/` and `call/` import. They are named for the domain, not
for the vendor behind them. The load-bearing rule is one-directional: **`call/` must never import
`a2a/`**, which makes the seam between the two layers exactly ADR 0001.

Calls run **in-process**, as asyncio tasks in the A2A server process. `a2a-sdk` cannot wake a running
executor — `execute()` must return to yield at `input-required` and the answer arrives as a fresh
call — so a live call is **parked in a registry keyed by `task_id`** and looked up when the answer
arrives. **`a2a/` owns that registry**; `call/` hands out opaque handles and never learns what they
are keyed by. The **transport is chosen by deployment configuration**, never by the Brief. Task state
is held in `InMemoryTaskStore`.

## Considered options

- **A subprocess per call**, as `docs/initial-research.md` recommends, was rejected for v1. The
  registry is mandatory under *either* model, and the Pipecat push into a live call
  (`worker.queue_frame(...)`) is an in-process API — so a subprocess design would build IPC purely to
  reach a dict it could otherwise hold. That advice was written for scale and isolation this
  deployment does not have.
- **A registry inside `call/` with its own id space** was rejected: defensible on lifecycle grounds,
  but it costs a second id space to solve what an opaque handle already solves.
- **A watchdog retaining the `event_queue`** to emit between `execute()` calls was rejected. It works
  — proven by probe — but violates the documented executor contract, and the event it would deliver
  ("the Callee hung up while we waited") is **not actionable**: the Client must supply the same
  answer either way, and learns the call ended at the same moment regardless.
- **Inferring the transport** from whether the Brief carries a phone number was rejected: it couples
  ADR 0001's contract to a deployment detail, and the Brief always carries a number anyway.
- **SQLite `DatabaseTaskStore`** was considered for durable eval records and rejected for v1 in favour
  of staying dependency-free. It buys no resumability regardless — the phone leg dies with the
  process.

## Consequences

- **A crashing call takes the A2A server with it**, and a wedged pipeline cannot be killed
  independently. Accepted for one laptop and one or two concurrent calls. **The registry is itself
  the seam** that makes a later move to subprocesses a swap rather than a rewrite.
- **The eval harness must capture its own records.** With an in-memory store, nothing durable
  survives a restart for it to score against.
- **A Callee hanging up mid-stall is invisible to the Client until it answers.** Bounded by the
  stall budget plus the Principal's response time.
