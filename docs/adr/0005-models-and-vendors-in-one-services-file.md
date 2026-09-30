# Models and vendors in one services file

Everything about which model and vendor fills each slot of a call lives in **one file**,
`services.yaml`, read from the working directory like `.env`, and **one module**,
`call/services.py`, that owns the file's shape, its checks and the building. Secrets stay in `.env`;
the file names the environment variable that holds each slot's API key.

- **LLM**: always OpenRouter, so the file gives only the model.
- **STT and TTS**: any Pipecat service. The file names the class **by its import path**, plus the
  settings applied over the service's own defaults. The TTS slot also gives one voice per Brief
  language. One generic builder serves both, relying on the construction contract every Pipecat 1.x
  speech service shares: an API key, plus a vendor `Settings` object applied over its defaults.

The module's interface is `ServicesConfig`: a pydantic-settings class whose only source is the file,
and whose `build(brief, system_instruction)` returns the call's services. `CallConfig` holds one as
its `services` field and knows nothing about models, vendors or keys. `services.py` never imports
`CallConfig`.

**The Brief's language and the voice are set by code, never by the file's settings.** The builder
applies them last, over the configured settings, so no configuration can break ADR 0001's rule that
the Brief decides the language of the call. The voice comes from the file's `voices`, and nowhere
else: there is no environment override.

**Every mistake fails at startup.** `bot.py` loads the configuration once, as it starts, and loading
rejects:
- a path that does not import, or a class in the wrong slot;
- a settings key that is not a declared field of the vendor's `Settings` (Pipecat would keep it in
  an overflow instead of failing, so a typo would do nothing);
- a Brief language with no voice;
- an unset key variable;
- an unknown top-level key in the file.

API keys are read once, when the file loads. The checks are kept to these on purpose: constructor
arguments beyond the API key and settings, and vendor keys outside the declared settings, are not
supported until a vendor needs them.

## Considered options

- **A name-to-class registry in code** (`{"deepgram": DeepgramSTTService}`) was rejected. It needs a
  code edit per vendor, which is the thing this decision removes, and it imports every vendor's
  extra whether installed or not. An import path follows the precedent the eval suite already set
  with `factory: evals.models.openrouter`.
- **A wrapper class per vendor** was rejected. Each would only pass values through to the Pipecat
  class, and deleting one would remove no complexity.
- **A typed pydantic model per vendor** was rejected. It checks types more strictly, but needs a new
  class for every vendor.
- **Speech slots in the file, the LLM model and key on `CallConfig`** was tried first and rejected:
  the model choice and the key mechanism were split across four places, and an environment voice
  override reached into the builder.
- **Pipecat's `ServiceSwitcher`** solves a different problem: switching services inside a live call,
  for failover. It stays available for that later.

## Consequences

- **Swapping to a drop-in vendor is a file edit plus installing its Pipecat extra.** Vendors that
  change turn-taking (Deepgram Flux) or the pipeline's shape (speech-to-speech) are not drop-ins and
  still need runtime changes.
- **Vendors whose credentials are not one API key** (Google, AWS) do not fit yet.
- **Trying a model or voice for one run means editing the file**; no environment variable overrides
  it.
- **A TTS vendor that loads can still fail on the line.** Loading checks the file, not the vendor's
  account: a model the endpoint rejects, or a plan that refuses the voice, only shows once a call
  connects. Text-mode evals (ADR 0004) do run the TTS service and catch this: with TTS dead, the
  agent's speech never starts, so the Disclosure repeats on every turn and Scenarios fail. What
  text mode cannot judge is how the voice sounds, so whether a vendor speaks Czech well still needs
  audio-mode Scenarios or the narrowband check.
- **The tests never read the shipped file's vendors.** They run against a services file of their
  own, and one test checks that the shipped file loads and builds for every Brief language, so
  changing vendors in `services.yaml` breaks at most that one test.
- **API keys are read from the process environment.** `bot.py` loads `.env` into it; code that
  builds `CallConfig` without doing so must set the key variables itself.
- **Changing the file needs a restart.** The bot loads it once, as it starts, and every call, eval
  runs included, uses that copy.
