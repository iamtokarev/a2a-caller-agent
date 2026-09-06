# A2A Voice Agent

A voice agent that places outbound phone calls on a person's behalf, exposed to other agents over the
A2A protocol. This glossary fixes the language used across the codebase and the docs.

## Language

### The parties

**Client**:
The peer agent that delegates a call over A2A and receives the Outcome.
_Avoid_: caller, requester, user — on a phone line the caller is *this* agent, so the word is never
free to mean the delegating peer.

**Principal**:
The human on whose behalf a call is made, and who is named in the Disclosure. Distinct from the
Client: the Client supplies the errand, the Principal is who it is for.
_Avoid_: user, owner, end user

**Callee**:
The person or business that answers the phone.
_Avoid_: recipient, target, called party

### The instruction

**Brief**:
Everything a Client hands over to have one call placed.
_Avoid_: request, payload, job spec, task (reserved for the A2A sense)

**Objective**:
What the call is meant to achieve, stated in free text. Exactly one per Brief.
_Avoid_: goal, intent, task

**Constraint**:
A condition the Objective must satisfy, marked negotiable or not. A non-negotiable Constraint is
what the agent may not break alone.

### The call

**Disclosure**:
The statement, made first in every call, that this is an AI and which Principal it acts for. Required
by EU AI Act Art. 50, not a courtesy.

**Escalation**:
A pause in which the agent puts a question it has no authority to answer to the Client, and waits for
an answer before continuing.

**Call-back**:
A second call to the same Callee, placed to finish an Objective after an Escalation could not be
resolved while the line was open.

### The answer

**Outcome**:
The structured result returned when a call ends. Data the Client can act on, not a narrative.
_Avoid_: report, response

**Disposition**:
What happened to the phone call itself, independent of the errand — whether anyone was reached, and
who.

**Result**:
Whether the Objective was achieved, independent of what the phone call did. A Callee who answers and
declines is a successful Disposition with a negative Result.
