# The Brief / Outcome contract between Client and voice agent

A Client delegates a call by sending a **Brief**: a free-text Objective plus typed fields the system
branches on — Callee (E.164 number and a human-readable name), Constraints (free-text descriptions
each flagged negotiable or not), Principal (name required, contact phone optional), language, and an
optional `useful_until`. The agent answers with an **Outcome** carrying two orthogonal fields —
Disposition (`connected` / `voicemail` / `no_answer` / `wrong_number` / `dial_failed`) and Result
(`achieved` / `not_achieved` / `undetermined`) — plus a prose `summary` and an open `details` map.
Mid-call, an **Escalation** poses a free-text question with an optional list of enumerated options,
answered by choosing one or replying in free text.

## Considered options

- **A fully typed Objective** (a `BookingRequest`) was rejected: it makes the Outcome trivially
  checkable but turns an errand-agnostic capability into a booking bot, contradicting the stock
  checks, reschedules and delivery chases named in `PRODUCT-OVERVIEW.md`.
- **A fully free-text Brief** was rejected: with no typed Constraint flag, whether the agent may bend
  a condition depends on the LLM's reading of prose, which is the road to inventing a decision on
  the Principal's behalf — the worst failure mode the product names.
- **A single Outcome enum** (booked / not booked / alternative offered / needs follow-up) was
  rejected on two counts: the names are booking-specific, and one enum conflates whether the *phone
  call* worked with whether the *errand* succeeded. A restaurant that answers and says it is full is
  a fully successful call with a negative Result; a disconnected number is not. Splitting the axes
  stops that landing on the Client as a failed task.
- **A per-Constraint verdict in the Outcome** was considered and dropped for v1 (see Consequences).
- **Number lookup by the agent** was rejected: resolving "Ambiente, Prague" to a phone number is a
  search job belonging to the Client, which is the party that has search tools.

## Consequences

- **Task-success scoring is LLM-judged, with no deterministic fallback.** Dropping the per-Constraint
  verdict means nothing in the Outcome mechanically reports whether a non-negotiable Constraint held;
  a judge must infer it from `summary`. Accepted knowingly — it is noisier than a boolean would have
  been.
- **The Outcome carries no transcript.** Prose lives in `summary` only. This keeps a recording of an
  identifiable person from crossing the A2A boundary, where retention is no longer ours to control.
- **`language` and `principal.name` are required with no defaults**, because both are spoken aloud in
  the Disclosure before anyone can correct a wrong guess.
- **The 5-minute call cap is configuration, not contract** — every errand wants the same answer, so a
  Client has no business tuning it. `useful_until` stays in the Brief because only the Client knows
  when an errand goes stale, and the Call-back path needs something to check.
