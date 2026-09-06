# Czech and English on one pipeline

Research for [05 — Czech and English on one pipeline](../../.scratch/v1-end-to-end/issues/05-czech-and-english-on-one-pipeline.md).
Sources checked 2026-09-06 against current vendor documentation and Pipecat source at the commit the
`pipecat-context-hub` index is built from (`5ff3201`, pinned to `pipecat-ai` 1.8.1). This document
records **facts and options**, not a vendor choice.

`docs/initial-research.md` was written before anything was committed. Where its claims are now wrong
or incomplete, that is called out under [What `initial-research.md` got wrong](#what-initial-researchmd-got-wrong).

---

## The one fact that reshapes everything

**Czech is not in any vendor's code-switching set.** Every multilingual-in-one-stream feature on
offer covers the same ten Western/major languages, and Czech is in none of them:

| Feature | Languages covered | Czech? |
| --- | --- | --- |
| Deepgram Nova-3 `language=multi` | en, es, fr, de, hi, ru, pt, ja, it, nl | **No** |
| Deepgram Flux `flux-general-multi` | en, es, fr, de, hi, ru, pt, ja, it, nl | **No** |
| Deepgram Aura TTS (all voices) | en, es, de, fr, nl, it, ja | **No** |
| Pipecat Smart Turn v3 | 23 languages (ar, bn, zh, da, nl, de, en, fi, fr, hi, id, it, ja, ko, mr, no, pl, pt, ru, es, tr, uk, vi) | **No** |

Sources:
[Nova-3 model/language table](https://developers.deepgram.com/docs/models-languages-overview.md) —
"`nova-3` or `nova-3-general` | **Multilingual (English, Spanish, French, German, Hindi, Russian,
Portuguese, Japanese, Italian, and Dutch): `multi`** … Czech: `cs`, `cs-CZ`" (Czech appears only in
the monolingual list, not in `multi`);
[Flux Multilingual & Language Prompting](https://developers.deepgram.com/docs/flux/language-prompting.md) —
supported-languages table lists exactly `en, es, fr, de, hi, ru, pt, ja, it, nl`;
[Aura voices and languages](https://developers.deepgram.com/docs/tts-models.md) — "Deepgram's Aura
text-to-speech supports the following languages: English, Spanish, German, French, Dutch, Italian,
Japanese";
[smart-turn README](https://github.com/pipecat-ai/smart-turn) — "Support for 23 languages" with the
list above.

Czech **is** supported by Nova-3, but only as a monolingual model (`cs` / `cs-CZ`), added in the
[4 November 2025 changelog](https://developers.deepgram.com/changelog/2025/11/4.md) ("Nova-3 supports
11 new languages … Czech (`cs`)") and confirmed in the
[18 November 2025 self-hosted release](https://developers.deepgram.com/changelog/2025/11/18.md).

Three consequences follow, and they are the substance of this ticket:

1. **Czech + English code-switching in one Deepgram stream is not on offer.** Either the STT is
   pinned to one language, or a different STT vendor is used (see [Soniox](#option-c--soniox-the-only-vendor-that-documents-czechenglish-mixing)).
2. **Deepgram Flux is unavailable for Czech calls.** Flux is Deepgram's turn-aware model — the thing
   that makes end-of-turn detection a model decision rather than a silence timer. Czech calls fall
   back to Silero VAD.
3. **Smart Turn v3 does not cover Czech either**, so the fallback is *plain* VAD, not smart turn.
   The Czech leg of v1 has strictly worse turn-taking machinery available than the English leg.

Deepgram TTS is settled by the same table: `flux-hannah-en` in `pipecat-bot/bot.py` is English-only,
and no Aura voice speaks Czech. **The TTS vendor must change regardless of anything else in this
document.**

---

## 1. Fixed per call, or detected?

Settled at the contract level: the Brief carries a required `language` with no default
([ADR 0001](../adr/0001-brief-and-outcome-contract.md), ticket 01). This section covers the
mechanics that decision implies and the recovery path if the Callee answers in the other language.

### What Deepgram does when the Callee speaks the other language

This is the sharpest risk, and Deepgram documents it plainly:

> When a specific language is set using the `language` parameter (e.g., `language=en`), Deepgram will
> only attempt to transcribe speech in that specified language. Speech in other, non-specified
> languages **will not be transcribed**.
> — [Languages Support](https://developers.deepgram.com/docs/language.md)

So the failure mode of an English-configured call answered in Czech is not *garbled transcripts* —
it is **silence in the pipeline**. The LLM sees no user turn at all. Any recovery path has to be
driven by something other than a bad transcript: a VAD-said-speech-but-STT-said-nothing signal, or a
timeout.

### Switching language mid-call in Pipecat

Pipecat supports it, and the cost differs per service.

**STT (Deepgram, standard `DeepgramSTTService`)** — a language change is a reconnect:

> **Runtime settings updates**: Changing settings via `STTUpdateSettingsFrame` triggers a
> reconnection with the new parameters. To avoid audio loss, reconnection is deferred until the
> current user turn ends (i.e., until `UserStoppedSpeakingFrame` is received). Audio frames arriving
> during the reconnect are buffered and replayed once the new connection is ready.
> — [Deepgram STT, Notes](https://docs.pipecat.ai/api-reference/server/services/stt/deepgram.md)

So the switch is safe (buffered, no audio loss) but costs a WebSocket round-trip and cannot land
mid-utterance. The utterance that *triggered* the realisation is the one that was already dropped.

The frame API is generic ([Service Settings](https://docs.pipecat.ai/pipecat/fundamentals/service-settings.md)):

```python
await worker.queue_frame(
    STTUpdateSettingsFrame(delta=DeepgramSTTService.Settings(language=Language.CS))
)
```

Update-settings frames are uninterruptible — they are processed even if the user barges in.

**STT (Deepgram Flux)** — `language_hints` is updatable *without* a reconnect (sent as a `Configure`
message over the live WebSocket), and the docs even name the pattern "detect-then-lock: narrow
language hints mid-stream". This is the nicer mechanism, and **it is unavailable to us**, because
`flux-general-multi` has no Czech.

**TTS (Cartesia)** — cheapest of the three. `language` rides on every synthesis message, and Pipecat
only flushes the current Cartesia *context*; the WebSocket stays up:

> Voice, model, and language are locked per Cartesia context. If any of these change, the current
> context is flushed so the next sentence opens a fresh one with the updated settings.
> — [`CartesiaTTSService._update_settings`](https://github.com/pipecat-ai/pipecat/blob/5ff3201996bae0be588191b2ea06ac336e057e16/src/pipecat/services/cartesia/tts.py#L219-L783)

**TTS (ElevenLabs)** — most expensive:

> Fields that appear in the WebSocket URL (`voice`, `model`, `language`) require a full reconnect
> when changed.
> — [`ElevenLabsTTSSettings`](https://github.com/pipecat-ai/pipecat/blob/5ff3201996bae0be588191b2ea06ac336e057e16/src/pipecat/services/elevenlabs/tts.py#L138-L172)

That is a full TTS WebSocket teardown and handshake in the middle of a live phone call, on top of the
STT reconnect happening at the same moment.

**Detection, if it is ever wanted.** Deepgram's `detect_language` covers Czech, but the parameter is
flagged `Pre-recorded  Streaming:Nova` — i.e. not a Nova-3 streaming feature — and Deepgram's own
advice is to use the multilingual models instead
([Language Detection](https://developers.deepgram.com/docs/language-detection.md)). For a Czech/English
pair, that advice does not apply, because `multi` has no Czech. There is no clean Deepgram path to
"detect which of Czech or English this Callee is speaking, live."

---

## 2. Code-switching

Czech speakers dropping English words ("máme rezervaci na *booking*", "v *open space*") and vice
versa is common, and no vendor claims to handle it for this pair.

### STT

- **Nova-3 `language=cs`**: transcribes Czech only; English words in a Czech sentence are, per the
  language-restriction paragraph above, not transcribed. There is no documented behaviour for
  loanwords vs. full English phrases — the boundary is exactly the kind of thing ticket 09 can
  measure.
- **Nova-3 `language=multi`**: would handle code-switching, but not for Czech.
- **Keyterm prompting is available for Czech.** Keyterms are Nova-3-wide, "available for both
  monolingual and multilingual transcription using the Nova-3 Models", up to 100 terms
  ([Keyterm Prompting](https://developers.deepgram.com/docs/keyterm.md)). This is the practical lever
  for a booking call: the restaurant name, the Principal's name, a street name, English words the
  Callee is likely to reach for. Note the silent-failure trap the doc calls out — commas or
  semicolons between terms are accepted and boost nothing.

### TTS

- **ElevenLabs states the opposite of code-switching support**: "the AI automatically detects the
  language based on text context, so **mixing languages in a single prompt should be avoided**. Via
  the API, language can be explicitly defined using the ISO 639-1 `language_code` parameter."
  ([Playground / TTS product guide](https://elevenlabs.io/docs/product-guides/playground/text-to-speech))
- **Cartesia documents code-switching for exactly one pair: Hinglish.** "Sonic supports
  code-switching between Hindi and English (Hinglish) in a single generation."
  ([Advanced capabilities](https://docs.cartesia.ai/build-with-cartesia/capability-guides/advanced-capabilities.md))
  Nothing is claimed for Czech + English.

So on the output side, the realistic v1 posture is: **the agent does not code-switch**. It speaks one
language per call and the prompt instructs it to stay there. Deepgram's own multilingual-agent guide
endorses steering this from the prompt rather than the model — "Force the agent to speak one language
regardless of user input → *'Always respond in English, even if the user speaks another language.'*"
([Multilingual Voice Agents](https://developers.deepgram.com/docs/multilingual-voice-agent.md)).

---

## 3. TTS: one voice or two?

### The vendor claims

**ElevenLabs** — accent comes from the voice, not the language code:

> Accents originate from the selected voice; **using a voice trained in the target language ensures
> proper pronunciation and intonation.**
> — [Playground / TTS product guide](https://elevenlabs.io/docs/product-guides/playground/text-to-speech)

**Cartesia** — makes the same point mechanically, with a documented fallback. A voice carries an
`accents` list; requesting a locale the voice has no accent for does not fail, it falls back:

> When you request a language this voice has no accent for, the voice will speak it with its original
> accent … Jacqueline doesn't support a Japanese accent, so … she speaks Japanese with an American
> accent.
> — [Multilingual Voices](https://docs.cartesia.ai/build-with-cartesia/capability-guides/multilingual-voices.md)

That is the honest statement of the one-voice option: **one voice can speak both, but it will sound
foreign in at least one of them unless it carries a native accent for both.** Whether a given voice
carries a `cs-CZ` accent is readable per voice from `GET /voices/{id}` (the `accents` field, with
`is_native`) — a concrete, cheap check ticket 09 should do before synthesising anything.

Note `locale` requires `sonic-3.6` or newer; on earlier models use `language` with a base code, and a
request may set one or the other, never both.

### Czech coverage per candidate

| Vendor / model | Czech | Evidence |
| --- | --- | --- |
| Cartesia `sonic-3.6` | **Yes** (`cs`, one of 44) | [Sonic 3.6](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest.md) — language table and `sonic-3.6-2026-08-27` snapshot list both include `cs` |
| ElevenLabs `eleven_flash_v2_5` | **Yes** (32 languages) | [Models](https://elevenlabs.io/docs/models.md) — "All `eleven_multilingual_v2` languages plus `hu`, `no`, `vi`"; the v2 list includes `cs` |
| ElevenLabs `eleven_multilingual_v2` | **Yes** (29 languages, `cs` listed) | same page |
| ElevenLabs `eleven_v3` / `eleven_v3_conversational` | **Yes** (70+, "Czech (ces)") | same page |
| Deepgram Aura / Flux TTS | **No** | [TTS models](https://developers.deepgram.com/docs/tts-models.md) |

### The ElevenLabs gotcha in Pipecat

Pipecat's ElevenLabs integration only sends `language_code` for four models, and
`eleven_multilingual_v2` is not one of them:

> Four models take a language code — `eleven_flash_v2_5` and `eleven_turbo_v2_5` cover 32 languages,
> and `eleven_v3` and `eleven_v3_conversational` cover 74 … Any other model, `eleven_multilingual_v2`
> included, **detects the language from the text itself; setting `language` there logs a warning and
> the code is dropped.**
> — [ElevenLabs TTS, Notes](https://docs.pipecat.ai/api-reference/server/services/tts/elevenlabs.md)

Verified in source: `elevenlabs_language_code()` looks the model up in `ELEVENLABS_MODEL_LANGUAGES`
and returns `None` (with a warning) for anything not in it
([`tts_base.py#L173-L204`](https://github.com/pipecat-ai/pipecat/blob/5ff3201996bae0be588191b2ea06ac336e057e16/src/pipecat/services/elevenlabs/tts_base.py#L173-L204)).
`Language.CS` maps to `"cs"` in Pipecat's table
([`tts_base.py#L207-L296`](https://github.com/pipecat-ai/pipecat/blob/5ff3201996bae0be588191b2ea06ac336e057e16/src/pipecat/services/elevenlabs/tts_base.py#L207-L296)),
and `Language.CS → "cs"` likewise for Cartesia
([`cartesia/tts.py#L78-L135`](https://github.com/pipecat-ai/pipecat/blob/5ff3201996bae0be588191b2ea06ac336e057e16/src/pipecat/services/cartesia/tts.py#L78-L135)).

Practical effect: with ElevenLabs the realistic Czech choices in Pipecat are `eleven_flash_v2_5`
(explicit `cs`, ~75 ms) or the v3 models — and the v3 models are only reachable through
`ElevenLabsDialogueTTSService`, which "requires workspace access to ElevenLabs' Text-to-Dialogue
WebSocket API" (same Notes section). That is an account-level gate, not a config flag.

### Latency claims (vendor-stated, excluding network)

| Model | Claim | Source |
| --- | --- | --- |
| `eleven_flash_v2_5` | ~75 ms | [ElevenLabs Models](https://elevenlabs.io/docs/models.md) |
| `eleven_v3_conversational` | ~280 ms | same |
| `eleven_multilingual_v2` | "higher latency & cost per character than Flash" | same |
| Cartesia `sonic-3.6` | "our fastest, most natural" — no number published on the model page | [Sonic 3.6](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest.md) |

### Swapping a voice mid-call

If two voices are used (one per language), the swap cost is the reconnect cost from §1: Cartesia
flushes a context (cheap, WebSocket survives), ElevenLabs reconnects the WebSocket (expensive). From
the Callee's side, a voice swap mid-call means the person they were talking to is suddenly a
different person — worth weighing against simply committing to one voice for the whole call, since
the Brief already fixes the language before the Disclosure is spoken.

---

## 4. Text normalization — the sleeper issue for a booking call

A booking call is made almost entirely of the categories TTS normalizers handle badly: times, dates,
party sizes, phone numbers. Both vendors document limits, and neither documents Czech specifically.

**ElevenLabs Flash v2.5 does not normalize numbers by default:**

> When using Flash v2.5, numbers aren't normalized by default in a way you might expect. For example,
> phone numbers might be read out in way that isn't clear for the user. Dates and currencies are
> affected in a similar manner. … **Enterprise customers** can now enable text normalization for v2.5
> models by setting `apply_text_normalization` … best practice is to have your LLM normalize the text
> before passing it to the TTS model.
> — [ElevenLabs Models, Considerations](https://elevenlabs.io/docs/models.md)

Multilingual v2 "does a better job of normalizing numbers" — but that is the model whose
`language_code` Pipecat drops. So with ElevenLabs the choice is: Flash v2.5 with explicit Czech and
no number normalization (unless Enterprise), or Multilingual v2 with normalization and
text-inferred language.

**Cartesia normalizes automatically and is locale-aware, but has no Czech locale.** `normalization`
accepts `auto` (default), `off`, or a locale code — and the supported-locale table covers only
English, Spanish, French, Dutch and Portuguese variants
([Advanced capabilities](https://docs.cartesia.ai/build-with-cartesia/capability-guides/advanced-capabilities.md)).
Czech falls to `auto`, which the docs say reads "according to the conventions of the request's
language". Known limitations that bite a booking call directly: hyphenated ranges are not normalized
in *any* language ("Dec 5-Dec 12", "$10-15" — write "to" instead), and fractions are not normalized
([Text Normalization](https://docs.cartesia.ai/build-with-cartesia/capability-guides/text-normalization.md)).

Czech makes this worse in a way no vendor doc addresses: Czech numerals and times decline
("v 19:30" → "v půl osmé", "pro 4 osoby" → "pro čtyři osoby"), so a normalizer trained mostly on
English conventions has more room to be wrong. **The safe posture for v1 is to have the LLM emit
already-spoken forms in Czech** — which is a prompt requirement, not a vendor setting, and belongs in
the booking-conversation ticket ([08](../../.scratch/v1-end-to-end/issues/08-booking-conversation-on-webrtc.md)).
Ticket 09 should include a time, a date, a party size and a phone number in its Czech utterance set
precisely to expose this.

---

## 5. What degrades at 8 kHz — the claims ticket 09 will test

Vendor documentation on narrowband is thin. What exists:

**Deepgram** — the only quantitative-ish statement found:

> **Sample rate:** Deepgram models are trained across the full range of audio quality. The sweet spot
> is **16 kHz** — there is no accuracy gain above this. If your telephony audio is band-limited to
> 8 kHz, **upsampling to a higher rate provides no benefit**.
> — [Audio Preprocessing & Barge-In](https://developers.deepgram.com/guides/deep-dives/audio-preprocessing-barge-in.md)

Note what this does *not* say: it does not quantify the 8 kHz penalty, and it says nothing
Czech-specific. It also kills one tempting mitigation — resampling 8 kHz telephony audio up before
sending it to Deepgram buys nothing.

**There is no `nova-3-phonecall`.** Phone-tuned variants exist only for the legacy families
(`base-phonecall`, `enhanced-phonecall`, `nova-phonecall`, `nova-2-phonecall`) and **every one of them
is English-only** ([models overview](https://developers.deepgram.com/docs/models-languages-overview.md)).
The `initial-research.md` line about "Deepgram's phone-call-tuned models … mitigate this" does not
apply to Czech, or to Nova-3 at all.

The same page's advice to skip noise suppression is worth carrying into ticket 09: "Always test
without preprocessing first… enterprise customers consistently report lower transcription accuracy
after applying noise suppression", with the risk highest for "accented or quiet speakers" and "short
utterances… names, 'yes', 'no', and single-word answers" — i.e. exactly a booking call's payload.

**Cartesia** publishes native narrowband output formats, including the European codec:

```json
{ "container": "raw", "encoding": "pcm_alaw",  "sample_rate": 8000 }   // G.711A — EU/international
{ "container": "raw", "encoding": "pcm_mulaw", "sample_rate": 8000 }   // G.711μ — NA/Japan
```

— [TTS output audio format](https://docs.cartesia.ai/build-with-cartesia/capability-guides/tts-output-audio-format).
`CartesiaTTSService` exposes `encoding` (default `pcm_s16le`) and `container` as constructor
arguments, so PCMA-at-8 kHz straight from the model is reachable without a resampling step
([Cartesia TTS](https://docs.pipecat.ai/api-reference/server/services/tts/cartesia.md)). No claim is
made about quality loss.

**ElevenLabs** supports `ulaw_8000`, `alaw_8000` and `pcm_8000` output formats, and notes μ-law "is
commonly used for Twilio audio inputs"
([Text to Speech convert](https://elevenlabs.io/docs/api-reference/text-to-speech/convert)). But
Pipecat only ever asks ElevenLabs for PCM — `output_format_from_sample_rate(8000)` returns
`"pcm_8000"`, never `ulaw_8000`
([`tts_base.py#L299-L326`](https://github.com/pipecat-ai/pipecat/blob/5ff3201996bae0be588191b2ea06ac336e057e16/src/pipecat/services/elevenlabs/tts_base.py#L299-L326)),
so the G.711 encoding happens in Pipecat's telephony serializer. Practical consequence for
**ticket 09**: to reproduce what the Callee will actually hear, synthesise at 8 kHz PCM (not at
24 kHz then downsample) and then apply the μ-law/A-law round trip. Doing it the other way measures a
different pipeline than the one that will ship.

Neither TTS vendor publishes any narrowband quality claim at all. **That gap is the whole reason
ticket 09 exists**, and no amount of further documentation reading will close it.

---

## 6. LLM quality in Czech

Honest finding: **no primary source settles this.** OpenRouter is a router; its documentation covers
routing, fallbacks and model metadata search, not per-language quality
([OpenRouter docs](https://github.com/openrouterteam/docs)). No model vendor publishes a Czech
conversational benchmark, and the multilingual claims that do exist ("70+ languages") are about
coverage, not quality on a 3-minute narrowband booking call.

What can be said:

- `OpenRouterLLMService` is a thin OpenAI-compatible passthrough, so **any** OpenRouter model is
  reachable and the model id is a one-line change in `pipecat-bot/bot.py` (currently
  `openai/gpt-4o-mini`). Nothing about the bilingual question is locked in by the LLM layer.
- The failure modes that actually matter for Czech are *observable in a transcript* rather than
  benchmarkable in the abstract: wrong grammatical case in a booking phrase, an over-formal register
  (Czech vykání vs. tykání — a restaurant call is vykání throughout), and English leaking into a
  Czech turn.
- The text-normalization burden from §4 lands on the LLM. If the prompt is asked to emit spoken-form
  Czech numerals and times, model quality on that specific task matters more than general fluency.

**Recommendation for the map, not a decision:** this belongs to the eval harness
([10](../../.scratch/v1-end-to-end/issues/10-what-we-measure.md),
[12](../../.scratch/v1-end-to-end/issues/12-build-the-eval-harness.md)) as an LLM-judged transcript
axis with Czech-specific criteria, not to a vendor-doc research ticket. Picking a Czech LLM from
published claims would be guessing.

---

## Options and trade-offs

Presented as options; the choice is the human's.

### STT

#### Option A — Nova-3 monolingual, language pinned from the Brief

`DeepgramSTTService(settings=…Settings(model="nova-3", language=Language.CS))`, or `EN`.

- **For**: no new vendor; matches the settled contract exactly; best per-language accuracy (a single
  language hint is Deepgram's own advice for known-language calls); keyterm prompting available.
- **Against**: the other language is silently *not transcribed*; no Flux, therefore no model-native
  turn detection; recovery from a wrong-language Callee costs a reconnect and loses the triggering
  utterance.

#### Option B — Nova-3 pinned, plus a mid-call switch as a recovery path

Option A plus a detector that notices "VAD says speech, STT says nothing" and pushes an
`STTUpdateSettingsFrame` to flip the language (and a matching `TTSUpdateSettingsFrame`).

- **For**: keeps the primary mechanism simple; degrades to Option A if the detector never fires.
- **Against**: needs a detector Pipecat does not provide; the reconnect is deferred to end-of-turn,
  so recovery is at least one turn late; two reconnects (STT + TTS) land at once.

#### Option C — Soniox: the only vendor that documents Czech/English mixing

`SonioxSTTService` ships in Pipecat (`pipecat-ai[soniox]`). Soniox states:

> By default, you don't need to pre-select a language — the model automatically detects and
> transcribes any supported language. It also handles **multilingual speech seamlessly, even when
> multiple languages are mixed within a single sentence** or conversation.
> — [Language hints](https://soniox.com/docs/stt/concepts/language-hints.mdx)

Czech (`cs`) is in its 60+ language list, and "All languages are available in both: Real-time API …
and Async API"
([Supported languages](https://soniox.com/docs/stt/concepts/supported-languages.mdx)). Pipecat
exposes `language_hints`, `language_hints_strict` ("only transcribe in provided languages") and
`enable_language_identification`, which annotates tokens with language IDs and puts the detected
language on the `TranscriptionFrame`
([Soniox STT](https://docs.pipecat.ai/api-reference/server/services/stt/soniox.md)).

- **For**: the only documented answer to the code-switching question for this language pair;
  `language_hints=[CS, EN]` needs no mid-call reconnect at all; per-turn detected language is a free
  signal for driving TTS language.
- **Against**: a new vendor, a new key, and an unmeasured one — no Czech WER numbers, no narrowband
  claim, and Deepgram's Nova-3 Czech accuracy is at least a known quantity by reputation. Turn
  detection story unverified.

### TTS (the choice is live regardless — `flux-hannah-en` cannot speak Czech)

#### Option D — Cartesia `sonic-3.6`

- **For**: already a declared extra in `pyproject.toml`, so zero dependency churn; Czech is native
  (44 languages); language change costs a context flush, not a reconnect; native `pcm_alaw`/`pcm_mulaw`
  at 8 kHz, which is exactly the EU telephony codec; automatic locale-aware normalization.
- **Against**: no Czech normalization locale (falls to `auto`); no published Czech quality or
  narrowband claim; whether any voice carries a native `cs-CZ` accent must be checked per voice via
  `GET /voices/{id}`; code-switching documented only for Hinglish; no published latency number on the
  model page.

#### Option E — ElevenLabs `eleven_flash_v2_5`

- **For**: lowest published latency (~75 ms); explicit `language_code=cs`; `initial-research.md`'s
  preference for Czech naturalness; adding the extra costs nothing — `elevenlabs = []` in
  [pipecat 1.8.1's pyproject](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/pyproject.toml), i.e.
  no new transitive dependencies, just `uv add "pipecat-ai[elevenlabs]"`.
- **Against**: no number normalization by default (Enterprise-only override) — a real problem for
  times, dates and phone numbers; a language change forces a **full WebSocket reconnect** mid-call;
  vendor explicitly advises against mixing languages in one prompt.

#### Option F — ElevenLabs `eleven_v3_conversational`

- **For**: 70+ languages including Czech; most expressive realtime model; ~280 ms.
- **Against**: reachable only via `ElevenLabsDialogueTTSService`, which **requires workspace access to
  the Text-to-Dialogue API** — an account gate to clear before this is even testable; higher latency
  than Flash; audio tags leak into the LLM context as spoken text unless filtered.

#### Option G — ElevenLabs `eleven_multilingual_v2`

- **For**: better number normalization; 29 languages including Czech; most stable quality.
- **Against**: Pipecat **drops** `language_code` for this model — language is inferred from the text,
  which for a short Czech confirmation like "Ano." is thin evidence; higher latency than Flash.

### Turn detection (a consequence, not a free choice)

For Czech, both model-native options are out (Flux: no Czech; Smart Turn v3: no Czech). That leaves
Silero VAD with tuned `stop_secs`. An English-only call *could* use Flux or Smart Turn — but running
two different turn-detection strategies on one pipeline is a seam worth not opening in v1. The
cheapest coherent posture is **Silero VAD for both languages**, accepting worse English turn-taking
than the stack could otherwise give. This should be recorded wherever the turn-taking decision lands
([04](../../.scratch/v1-end-to-end/issues/04-pipecat-patterns-for-phone-calls.md)).

---

## What `initial-research.md` got wrong

| Claim in `docs/initial-research.md` | Status |
| --- | --- |
| "Deepgram Nova-3 … supports Czech" | **Correct.** Monolingual `cs` / `cs-CZ`, added Nov 2025. |
| "Deepgram's newer Flux model is purpose-built for voice-agent turn-taking" | **Correct but inapplicable.** Flux has no Czech in either variant. |
| "Deepgram's phone-call-tuned models … mitigate this [8 kHz loss]" | **Wrong for this project.** No `nova-3-phonecall` exists; every `*-phonecall` variant is English-only. |
| "up to 27 percent WER reduction [Czech] over Nova-2" | **Not found in current Deepgram documentation.** The Nov 2025 changelogs announce Czech support without publishing a WER figure. Treat as unverified. |
| "ElevenLabs Multilingual v2 covers 29 languages" | **Correct**, `cs` included — but Pipecat drops `language_code` for that model. Not mentioned in the research doc. |
| "Cartesia Sonic … 42 languages" | **Superseded.** `sonic-3.6` is GA with **44** languages, `cs` included, plus a `locale`/`accent` system the research doc predates. |
| "ElevenLabs … ~150 ms time-to-first-audio" | **Superseded.** Current published figures: Flash v2.5 ~75 ms, v3 Conversational ~280 ms. |
| "cascaded pipeline (Deepgram + ElevenLabs) gives you provider-level control over Czech quality" | **Still the right shape**, and now better justified: the vendors' multilingual features all stop short of Czech, so per-leg control is the only way to get Czech at all. |
| Nothing said about Czech + English code-switching | **The gap this ticket fills.** No vendor covers the pair except Soniox. |

---

## Facts later tickets depend on

Recorded here so [09](../../.scratch/v1-end-to-end/issues/09-czech-narrowband-sanity-check.md) and
the build tickets do not have to re-derive them.

- **STT language codes**: Deepgram Czech is `cs` (also `cs-CZ`); Pipecat's `Language.CS`.
- **Deepgram model id**: `nova-3` / `nova-3-general`. `nova-3-medical` is English-only. No phonecall
  variant.
- **Deepgram pins one language**: non-specified languages are not transcribed at all.
- **`language=multi` and `flux-general-multi` are both the same 10 languages** — never Czech.
- **Deepgram narrowband guidance**: 16 kHz is the sweet spot; do not upsample 8 kHz telephony audio.
- **Cartesia model id**: `sonic-3.6` (or the dated snapshot `sonic-3.6-2026-08-27` for repeatable
  evals — Cartesia recommends dated snapshots when consistent behaviour matters). Czech is `cs`.
  `locale` requires 3.6+; set `locale` or `language`, never both.
- **Cartesia telephony output**: `{"container":"raw","encoding":"pcm_alaw","sample_rate":8000}` for
  Europe. Settable via `CartesiaTTSService(encoding=…, container=…)`.
- **ElevenLabs models that accept an explicit Czech code in Pipecat**: `eleven_flash_v2_5`,
  `eleven_turbo_v2_5` (deprecated), `eleven_v3`, `eleven_v3_conversational`. Not
  `eleven_multilingual_v2`.
- **ElevenLabs at 8 kHz via Pipecat is `pcm_8000`**, not `ulaw_8000` — the G.711 step is Pipecat's.
- **Extras**: `pipecat-ai[elevenlabs]` and `pipecat-ai[soniox]` both declare **no** extra
  dependencies in pipecat 1.8.1; adding either is dependency-free.
- **Smart Turn v3 has no Czech**; Silero VAD is the Czech turn-detection floor.
- **Cartesia voice check**: `GET /voices/{id}` returns `accents` with `is_native`; use it to confirm a
  voice actually has a Czech accent before judging its Czech.

## Still open after this ticket

- Actual Czech WER for Nova-3, wideband vs. 8 kHz — **ticket 09**.
- Whether any Cartesia or ElevenLabs voice sounds credibly Czech through a phone — **ticket 09**,
  subjective listening, not a number.
- Soniox Czech accuracy and narrowband behaviour, if Option C is entertained. No public numbers.
- Czech LLM conversational quality — the eval harness, tickets 10 and 12.
- How the "Callee answered in the wrong language" signal is actually detected in a pipeline where the
  wrong language produces no transcript at all.
