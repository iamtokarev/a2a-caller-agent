# Product Overview

Status: draft — high-level product intent, not an implementation plan.

## What this is

A **voice agent that makes phone calls on a user's behalf**, exposed to other agents over the
[A2A](https://a2a-protocol.org) (agent-to-agent) protocol.

The agent has two faces:

- **Toward the phone network** — it dials a real number, talks to whoever picks up, handles the
  messiness of a live call, and hangs up with a result.
- **Toward other agents** — it is a peer that any A2A-speaking agent can hand a job to and get a
  structured answer back from.

## Why build it

Personal agents are good at everything that has an API and useless at everything that doesn't. A
large share of everyday errands — booking a table, asking whether something is in stock, rescheduling
an appointment, chasing a delivery — still ends in a phone call to a human. That is the gap this
agent fills.

Building it as an A2A peer rather than a standalone app makes it **reusable capability**: any
A2A-speaking agent gains "can make a phone call" without embedding voice, telephony, or call handling
into itself.

## How it gets used

The intended flow, end to end:

1. A user tells their personal assistant agent: *"book a table for four at Ambiente on Saturday
   around 7."*
2. That agent decides a phone call is the right move and delegates to this voice agent over A2A,
   passing the goal and the constraints.
3. The voice agent places the call, has the conversation, and adapts to what actually happens on the
   line.
4. If a decision comes up that isn't the agent's to make — *"we only have 20:30, is that OK?"* — it
   pauses and asks the calling agent, which asks the user, and the answer comes back into the live
   call.
5. The call ends and the voice agent returns a **structured outcome** — booked, not booked,
   alternative offered, needs follow-up — not a wall of transcript.

The caller is always another agent. There is no human-facing UI in scope: the user interacts with
their personal assistant, which interacts with this.

## What it is (and isn't)

**In scope**

- Outbound calls with a clearly stated goal, driven by a natural spoken conversation.
- Handling real phone conditions: menus, hold, voicemail, wrong number, a person who doesn't
  cooperate with the script.
- Reporting back a machine-readable outcome that the calling agent can act on.
- Asking the calling agent mid-call when it hits a decision above its authority.

**Out of scope, at least for now**

- Inbound calls / being a receptionist.
- Campaigns, sales, or any bulk outbound calling.
- Being a general chat assistant. This agent has one job: complete a specific call objective.
- A UI of its own.

## Shape of the system

Three layers, deliberately separable:

```
  Other agents (personal assistant, etc.)
              │  A2A: "place this call, here's the goal"
              ▼
  ┌──────────────────────────────────┐
  │  A2A interface                   │  long-running task, streamed progress,
  │                                  │  can pause to ask the caller a question,
  │                                  │  returns a structured outcome
  └──────────────┬───────────────────┘
                 │  one call = one conversation session
                 ▼
  ┌──────────────────────────────────┐
  │  Voice conversation (Pipecat)    │  listen / think / speak in real time,
  │                                  │  interruptions, turn-taking, call tactics
  └──────────────┬───────────────────┘
                 │
                 ▼
  ┌──────────────────────────────────┐
  │  Telephony                       │  dial out over the phone network
  └──────────────────────────────────┘
                 │
                 ▼
              A human
```

Why this split: the A2A layer is about **jobs** (accepted, in progress, blocked on input, done), the
voice layer is about **a conversation** (milliseconds, turns, interruptions), and telephony is a
commodity underneath. Each changes for different reasons and should be replaceable without touching
the others.

## Product principles

- **A call is a long-running job, not a request/response.** It takes minutes, its state is worth
  streaming, and it can get stuck waiting on a human. The interface must model that honestly rather
  than pretending a call is a function call.
- **The agent asks instead of guessing.** When reality diverges from the goal, escalate to the
  calling agent. Inventing a decision on the user's behalf is the worst failure mode.
- **It always says it's an AI, and who it's calling for.** Disclosure up front, every call. This is
  both the ethical default and an EU legal requirement (AI Act Art. 50).
- **The output is data, not a story.** Callers get a structured result they can act on; a transcript
  is at best a supporting detail.
- **One goal per call.** Narrow objectives are what make a voice agent reliable.

## What "good" looks like for v1

A single realistic errand, end to end: a personal assistant agent delegates a restaurant booking, the
call happens, a mid-call constraint gets bounced back to the user and answered, and the calling agent
receives a confirmed booking as structured data — with the call opening with an AI disclosure.

## Open questions

- How much authority does a call get by default before it must ask? (fixed policy vs. per-task
  budget passed by the caller)
- What belongs in the returned outcome beyond success/failure — transcript, recording, summary?
  (leans toward: structured outcome only, no audio retained)
- Should the agent also be reachable as a plain tool (MCP) for fire-and-forget calls, or is A2A the
  only entry point?
- Where does retry / callback live — inside a single task, or is a failed call simply a failed task
  the caller re-issues?

## Related

- [`docs/initial-research.md`](docs/initial-research.md) — technology and vendor research behind the
  choices hinted at here.
