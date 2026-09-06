# Escalation: stall on the line, then call back

When the agent hits a decision that is not its to make, it tells the Callee it is checking, holds the
line for a **stall budget of roughly 60 seconds** while the Client is asked, and if no answer arrives
in that time it **proactively offers a Call-back**, ends politely, and places a second call once the
Principal replies. Two triggers open an Escalation: the only way forward would break a
**non-negotiable Constraint**, or the Callee asks for information or a decision the Brief does not
cover. Bending a **negotiable** Constraint is explicitly *not* a trigger — the agent does it and
reports what it bent in the Outcome.

A Call-back continues the **same A2A task** (the Client answered on it, and the Outcome belongs to
the original Brief) but starts a **fresh conversation**: the Disclosure is repeated, the request is
restated compactly with the answer folded in, and only a short summary of the first call is carried
into context — never the previous transcript. `useful_until` is checked before dialling.

## Considered options

- **Detecting Callee impatience from tone or phrasing** was rejected. A false positive abandons a
  call that was seconds from succeeding, and there is no way to measure how often that happens. The
  stall budget carries the load instead, with explicit refusal ("can you call back") as the only
  other signal — handled as a tool call, not as sentiment.
- **Waiting for the Callee to complain** before offering a Call-back was rejected: a person holding
  a silent line has already been failed by the time they say so. The agent offers first.
- **Replaying the first call's transcript into the Call-back's context** was rejected — the second
  call may well reach a different person, who would be referenced saying things they never said.

## Consequences

- **The stall is bounded well inside the 5-minute call cap**, so the cap is a backstop rather than a
  mechanism. Silence during the stall is filled with an immediate acknowledgement and one check-in
  around 30 seconds.
- **A Callee who hangs up mid-stall lands on the Call-back path involuntarily** — the task stays in
  `input-required` and the Principal's answer, whenever it arrives, triggers the second call.
- **What happens when the Principal never answers is deliberately undecided** (see ticket 21). Until
  it is, such a task sits in `input-required` indefinitely.
