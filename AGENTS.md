# AGENTS.md

## Working style

- Make **surgical** changes: touch only the code the task needs. Leave unrelated code, formatting and comments as they are.

## Git

- Use conventional Commits: `type(scope): description`.
- Description: lowercase, imperative, 20 words max, no trailing period.

## Tracing

LangSmith is the tracing backend: one trace per call, holding its transcript and stereo audio (`src/a2a_voice_agent/call/observability.py`). It turns on with `LANGSMITH_TRACING=true`. Add new observability through LangSmith, and debug a call by fetching its trace with the `langsmith-trace` skill.

## Pipecat

The agent is built on the Pipecat framework. Query the Pipecat context hub for its docs and examples.

## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
