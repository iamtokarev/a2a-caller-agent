# Evaluation: Simulated Callees judged on one success criterion

The agent is evaluated with **Pipecat Evals** (pipecat-ai 1.12+). `make eval` runs one
`pipecat eval suite`, which is a **manual local gate**: any failed or errored run fails it. It is
not a CI job, because every run spends LLM tokens and needs the keys in `.env`.

The **primary layer is simulated Scenarios**. A Simulated Callee plays the restaurant from a
persona, and a judge decides one `success:` criterion over the whole conversation: did the call
end the way this Scenario says it should? The criterion includes the Outcome the agent
reported, since the judge sees every `report_outcome` and `ask_principal` call with its
arguments. Each simulation runs **three
times** on a fresh bot, and every run must pass.

The **secondary layer is a few scripted Scenarios** for exact beats:
- that the first reply discloses an AI acting for a named person, judged on intent rather than
  wording;
- `report_outcome` and `ask_principal` arguments, checked deterministically;
- voicemail.

Everything runs in **text mode**. One pinned OpenRouter model plays both the Simulated Callee
and the judge; it is a constant in the eval code and comes from a different model family than
the agent.

## Considered options

- **Deterministic Outcome matching as the definition of success** was rejected as the
  primary signal. `report_outcome` is a function call, so a scenario could assert
  `disposition` and `result` exactly. But that grades what the agent *claims*, not whether the
  conversation earned it. It stays in the scripted layer as a secondary check.
- **Per-reply quality metrics** (one question at a time, short turns, no restating, word and
  latency bounds) were rejected. On a capable model, generic rules pass nearly every time, and a
  check that never fails is cost and noise. Add a metric only for a failure that has actually
  been observed.
- **Building our own reactive stand-in Callee** (ticket 11) was rejected: Pipecat simulations
  provide one as YAML.
- **Latency and speech quality inside the harness** were rejected for v1. Text mode measures only
  the LLM's first token. Per-turn latency comes from LangSmith traces of real calls, and Czech
  speech quality from the standalone narrowband check (ticket 09). Audio-mode scenarios are a
  later follow-up.
- **Local Ollama (gemma4:12b) as judge and Simulated Callee**, the framework default, was
  rejected: its Czech is doubtful, and the Simulated Callee must hold a believable Czech
  conversation.
- **A custom scorecard or report** was rejected. Pipecat's suite output is the report: each
  failure's `kind` separates judged failures (`judge_no`) from deterministic ones, and the suite
  gives per-simulation pass rates and `results.jsonl`.

## Consequences

- **The bot reads each Scenario's settings from the suite's runner body.** These are the Brief
  to use (replacing `BRIEF_PATH`), an optional opening line, the Principal's answer, and the
  Scenario name. The opening line exists because a Simulated Callee only ever answers, while an
  outbound agent waits for the Callee to speak first. So in simulations the Callee is taken to
  have said a neutral "Hello?" in the Brief's language when the call connects.
- **`call/` gains only generic hooks, never knowledge of Scenarios.** `run_call` takes an
  optional line the Callee is taken to have said on connect, and extra tags for the call's
  trace. The `eval` environment is one more deployment environment beside `local` and `prod`.
  Everything that knows about the suite lives in `bot.py` and `evals/`.
- **The Principal is scripted.** Each suite entry carries the Principal's answer to an
  Escalation, or none. No answer returns immediately, which takes the same path as a stall
  budget running out, so no run waits out the budget.
- **The Stall is invisible in text mode.** Its fixed lines are speech, not LLM text, so neither
  the Simulated Callee nor the judge sees them. Escalation success criteria are worded around
  what is visible.
- **The judge itself is not validated.** A judge that gives no verdict makes the run an error,
  and an error fails the gate. Whether its verdicts are *right* is left until one looks wrong.
- **Eval Briefs are fictional**, like the test fixtures: no real Principal's name or number,
  and no check that depends on a name.
- **Eval runs are traced to LangSmith like any call**, when tracing is on. They are tagged with
  the Scenario name and an `eval` environment, so they never mix with real calls.
- **Evals pin the shipped call timings** (stall budget, call cap, cap warning), whatever a local
  `.env` tunes for manual calls. Pipecat's runner loads `.env` over the environment, so the
  eval run sets them explicitly.
