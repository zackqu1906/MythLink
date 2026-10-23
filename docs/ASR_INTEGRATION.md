# ProxiMic session -> ASR integration

This document describes **when** a near-speech utterance starts and ends. The
choice of ASR model is intentionally separate; see `ASR_BACKENDS.md`.

The Ring input, ProxiMic feature pipeline, CNN architecture, and checkpoint are
unchanged. The post-activation cooldown remains removed so the existing detector
continues producing Stage2 `ACTIVATE` / `reject` evidence.

## Runtime state machine

```text
IDLE
  |
  | Stage1 -> wait 0.3 s -> Stage2 CNN on the rolling 1 s window
  | first ACTIVATE
  v
ACTIVE / recording original 16 kHz PCM
  |
  | rolling Stage2 every 0.20 s while active
  | later ACTIVATE     -> same session, reject counter = 0
  | Stage2 reject      -> reject counter += 1
  | N rejects          -> END
  | no Stage1 for T s  -> END fallback
  | max duration       -> END safety bound
  v
one complete 16 kHz near-speech utterance
  |
  v
UtteranceSink
  |
  +--> one ASRWorker
  +--> ASRFanout -> multiple ASRWorkers
  +--> future WAV saver / benchmark sink
```

`ProximitySessionController` does not import or instantiate any concrete ASR
model. It only creates an utterance and calls `sink.submit(audio)`.

## Why an inactivity fallback is required

Consecutive Stage2 rejects are the primary endpoint. But if the user truly stops
speaking, Stage1 may stop triggering as well. In that case Stage2 never runs and
therefore cannot emit a reject. A session based only on reject count could remain
open forever.

ACTIVE mode therefore also ends after a configurable interval with no Stage1
trigger. This is detector-event inactivity, not RMS silence and not a second VAD.

Defaults:

```text
pre-roll                 1.00 s
ASR input gain            0.00 dB
Active Stage2 interval   0.20 s
Stage2 reject count      5
Stage1 inactivity        1.25 s
minimum utterance        0.40 s
maximum utterance       15.00 s
```

The inactivity timeout must remain longer than the initial Stage2 delay
(0.30 s), so it does not fire while a valid first Stage2 result is still pending.

## Confirmation-tail trimming

When consecutive rejects confirm END, the later rejects are useful as evidence
but its whole audio tail is not useful to ASR. The session controller therefore
cuts the submitted waveform at the first reject's Stage2 endpoint. This reduces
far-speech / ambient tail contamination.

## Why the 1.0 s pre-roll stays

The first Stage2 decision arrives only after the 0.3 s delay. Starting waveform
capture at the ACTIVATE event would lose the beginning of the command. The
controller keeps the latest 1.0 s of the original 16 kHz Ring waveform and prepends
it when a new session starts.

This never changes ProxiMic input. ProxiMic still performs its own 16 kHz -> 8 kHz
path internally; ASR keeps the 16 kHz timing and receives only the optional
downstream gain described below.

## ASR-only input gain

The desktop app applies its adjustable ASR gain only after ProxiMic has evaluated
the original Ring waveform. The enhanced 16 kHz waveform is then shared by the
ASR backend and voice history, so replayed history matches what recognition heard.
The UI defaults to 0 dB and permits 0 through +12 dB. For quiet speech, +6 dB is
a useful first trial; excessive gain can clip loud speech and reduce recognition
quality.

## ASR workers are asynchronous

### Diagnosing empty recognition from digital silence

`[ASR AUDIO]` records the final utterance's session ID, sample count, exact-zero
sample count, peak and RMS in `logs/diagnostic.log`. When at least two seconds
of final audio are entirely zero and the backend returns no text, streaming ASR
publishes a terminal error (`本句录音全为静音，请检查麦克风或重新连接`) through the
existing native input HUD error path. Short silence, quiet nonzero audio, and
actual transcripts are unaffected. This detects a silent recording; it does not
identify the defective hardware/software component or automatically restart MIC.

At the Ring ADPCM boundary, `[RING AUDIO HEALTH]` logs the first block, two-second
zero-PCM onset, continuing silence (at most every 30 seconds of wall time), recovery,
and close while still silent. Each event identifies the capture WAV, sequence,
device uptime, sequence gaps, zero-audio duration, PCM peak/RMS and hashes, and
encoded predictor/index/sample-count/nibble histogram. Only decoded-zero anomalies
include complete `encoded_hex` replay evidence, capped at three blocks per capture
(806 bytes each); ordinary speech is not copied into the log. Decoding that evidence
with an independent IMA ADPCM implementation distinguishes silent encoded input
from a decoder discrepancy. Logs rotate using the existing bounded diagnostic log.
No full-stream encoded packet history is retained.

The anomaly records also include `raw_notifications_hex`: a bounded copy of
notifications at `AudioProcessor.handle_notification`, before `MicBlockAssembler`.
Only matching sequence/uptime packets are emitted, in arrival order, including
duplicates. This is the Python BLE receive boundary, not an over-the-air trace.
The window holds at most 32 packets of 818 bytes and shares the three-anomaly
limit above. Long/abnormal fragmentation can truncate evidence; missing raw data
must not be interpreted as a successful SDK framing check. Normal speech packets
are not written to this diagnostic log.

Run `.runtime/venv/bin/python tools/verify_ring_audio_trace.py logs/diagnostic.log`
to independently assemble those notifications and decode them with Python 3.11's
stdlib `audioop`, without importing the SDK. It reports framing and PCM hash
mismatches, incomplete evidence, and old logs lacking raw packets separately.
This verifies the receive path only; it does not rule out a bad MIC control
command or firmware initialization sequence.

Input-method updates emit `installation_started`, `installation_blocked`, and
`installation_finished` through the existing `[EVENT INPUT_METHOD]` timeline.
The completion event includes the same success/failure message shown in the UI.
Align these with `connected`/`disconnected` and raw-audio recovery before treating
an apparent recovery after an input-method update as causal.

These diagnostics require restarting the source app; they cannot recover encoded
evidence that was not captured during an earlier incident.

### Worker threading

Each ASR backend gets an `ASRWorker`, so model inference / HTTP calls never block
the real-time Ring read loop. When several backends are selected, `ASRFanout`
submits the exact same utterance to each worker.

See `ASR_BACKENDS.md` for backend/model selection, comparison commands, cloud
integration, and the adapter template.

## Doubao semantic dialog context

Only the Volcengine/Doubao Seed-ASR backend receives optional editor context
before opening each utterance stream. On macOS, context comes from the native
input method's owned client, without searching for or selecting an input field.
Windows retains its read-only UI Automation adapter. Neither path selects text,
sends copy shortcuts, moves the caret or touches the clipboard. Unavailable
context never prevents ASR from starting.

The most recent text is split into sentence-like fragments and sent newest first
as the documented stringified `request.corpus.context` value with
`context_type=dialog_ctx`. The client caps the payload at 20 fragments and a
conservative 640 characters, below the service's 800-token limit. This path is
not wired to local or other cloud ASR backends.

## Short dictation punctuation

Doubao streaming still sends `request.enable_punc=true`: the documented switch
controls punctuation for the whole request, not just short utterances. Locally,
after `finish()` receives the complete result, dictations of at most 20 units
lose only their trailing sentence punctuation. Each Han character, English word
(including internal apostrophes/hyphens), or number is one unit; whitespace and
punctuation do not count. The limit applies to the entire recording, not each
server segment. Longer dictations retain all punctuation.

Internal punctuation, decimal points, and word symbols stay intact. Closing
quotes/brackets are not stripped, and ambiguous ASCII URL/abbreviation endings
are preserved. Streaming partials stay unchanged since a short prefix can grow
into a long utterance. This is a local ASR finalization rule, with no extra model
request and no changes to the separate editing or polishing prompts.

## Personal hotwords

The desktop passes a live `asr_hotwords_provider` to the runtime. Each utterance
gets an independent, immutable snapshot from `HotwordStore`; the backend merges
`hotwords: [{"word": "张玮"}]` with any dialog context inside the same stringified
`request.corpus.context`. Hotwords also work when editor context is unavailable.
Changing the dictionary does not restart a connection or mutate a running
utterance. Empty or failed vocabulary reads do not prevent ordinary recognition.
Malformed editor length metadata falls back to the actual text length so it cannot
discard the vocabulary. Each `[ASR CONTEXT]` log includes `hotwords=N`; `status=sent`
means the request configuration was written to the WebSocket, not that the service
guarantees recognition of those words.

The current `/api/v3/sauc/bigmodel` endpoint documents a 100-token hotword limit.
Without Seed's tokenizer, selection uses a conservative 90-unit UTF-8-byte proxy
(word bytes plus two units per entry), not an exact token count. Full vocabulary
stays on disk; the enabled prefix is selected in the user's saved display order,
stopping at the first entry that does not fit. Disabled entries consume no budget.
Version 2 persists this order in the entries array, migrating version 1 in its
previous visible priority order. New words prepend in extraction order; repeated
evidence never reorders existing words. UI drag previews use the same selector;
drop moves a stable ID before another ID, preserving concurrent additions.
Documented parameters:
https://docs.volcengine.com/docs/DoubaoVoice/LargemodelstreamingautomaticspeechrecognitionAPI?lang=zh

`HotwordController` stages the instruction and original text using transport
epoch, client, source utterance and edit revision. It only starts background
extraction after the native `edited` settlement confirms the expected result.
History undo uses the original source utterance, even when its current revision
belongs to a newer session. A cancelled job cannot publish a late result.
Legacy desktop edits use the existing successful-application hook; posting an
undo shortcut conservatively retracts learning without claiming verified undo.

The independent `HOTWORD_EXTRACTION_PROMPT` returns only a JSON array of up to
eight corrected words, such as `["张玮", "Java"]`, or `[]` for no corrections.
It does not request original words, evidence fields or correction categories.
Editing-mode instructions may omit “change to”: character explanations
such as “鼠标的鼠” and repetitions of the corrected phrase are explicitly covered.
The prompt interprets natural speech using all three texts, including short or
incomplete explanations, and retains lexical corrections within mixed instructions
that also request a rewrite. It distinguishes correcting how the same term is
written from changing the person, object or meaning.
The model judges correction intent and excludes ordinary content changes.
Local validation checks string types, word length, calendar/numeric exclusions,
and increased occurrences in the edited result, then deduplicates the words;
it does not independently verify instruction evidence or replacement intent.
Editing and polish tool schemas are unchanged. One daemon worker processes at
most 16 queued jobs, with a 10-second HTTP timeout and a
1,024-token output budget. Learning failures never change the applied text.
Only vocabulary and source identifiers persist, not whole editor documents.
Manual additions, disabled words, deletion tombstones and undo contributions
survive atomic JSON saves independently of the optional history collector.
`[HOTWORD_LEARNING]` traces preparation, native settlement, extraction and saving
by transaction identity. `[HOTWORD_EXTRACTION]` distinguishes a model's empty
array from local validation rejection (including individual reasons). These go
to the existing bounded diagnostic log; API keys and original document/instruction
text are not added to these events. An empty extraction is also reported in the
library status instead of silently leaving its previous message visible.

Only newly inserted, successfully saved words emit `learned(key, words)`.
The Python and native input controllers independently validate the edit identity
and phase before the presentation-only `hotwords_learned` command updates the
existing passive ActionPanel for four seconds. No text write, session transition,
extra field discovery, or focus activation is involved.
