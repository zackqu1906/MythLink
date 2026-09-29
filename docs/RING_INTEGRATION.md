# Ringo SDK integration

This project is no longer protocol-neutral: it is wired to the supplied
`ring-python-sdk` implementation.

## Integrated SDK snapshot

The bundled `src/ring_python_sdk` has been updated from the supplied
`ring-python-sdk-main` tree. The product-specific unfiltered device picker and
manual reconnect default are kept on top of that snapshot. On Windows the
picker passes only the selected address to the runtime, which resolves a fresh
`BLEDevice` in the same asyncio loop used for connection and notifications.

Connection-relevant changes in the newer SDK are:

- MIC recording control/status/list/read packets are separated from live MIC
  audio before codec reassembly. This prevents newer-firmware control packets
  from being interpreted as PCM/ADPCM/Opus fragments.
- Device time is synchronized after notifications start. The operation is
  best-effort and does not make a failed sync abort the connection.
- MIC start supports explicit hardware and software gain values while retaining
  the legacy three-byte command when no gain is supplied.
- Identity, temperature, reboot, flash health recording, MIC recording, and
  IMU/PPG calibration protocols are now available. Identity support adds the
  `cryptography` runtime dependency.

The SDK update does not change the live audio fragment assembler or codec
decoder, so it cannot by itself repair a real radio/firmware stream stop.

## Verified SDK audio contract

From the supplied SDK source:

- transport: BLE Nordic UART Service (NUS), handled by `RingSession` / Bleak;
- MIC modes: `pcm`, `adpcm`, `opus`;
- default SDK audio format after decoding: **16,000 Hz, mono, signed PCM16LE**;
- real-time API: `await session.mic_on(..., on_pcm=callback)`;
- callback signature: `callback(frame_seq: int, pcm: bytes)`;
- the SDK simultaneously saves decoded audio to a WAV file;
- Opus blocks contain 5 x 320-sample frames (= 1600 decoded samples / 100 ms per block).

The ProxiMic adapter therefore does not need to know BLE UUIDs, packet headers,
fragment assembly, ADPCM, or Opus internals.  The SDK owns those concerns.

## Runtime path

```text
Ringo microphone
  -> ring-python-sdk / BLE NUS
  -> MIC packet assembly + codec decode
  -> on_pcm(frame_seq, PCM16LE bytes)
  -> RingAudioSource
  -> float32 mono samples [-1, 1)
  -> runner.py
  -> detector.feed()
  -> Stage 1 / delayed Stage 2 / CNN
```

The WAV written by the SDK is a useful experiment/debug recording, but it is
**not** read back into the real-time inference path.

The desktop runtime connects BLE and validates NUS with MIC still off. It loads
the ProxiMic and ASR models first, then sends one MIC ON and requires a real PCM
callback before recognition starts. This matches the reliable firmware receiver
path and prevents heavy Python model initialization from starving Bleak's live
notification callbacks immediately after MIC ON.

On Windows, a `BLEDevice` created by the picker's temporary scan loop is never
passed into the separate runtime thread. The runtime scans for the selected
address again with targeted discovery, returning as soon as that address is
advertised and waiting at most 3 seconds. Scan, connect, and notifications still
share one asyncio/WinRT MTA thread just like the firmware receiver. The BLE
connection handshake keeps its normal timeout. On macOS the opaque CoreBluetooth
scan handle is retained because it cannot be reconstructed from a MAC address.

The supported Windows environment pins Bleak 3.0.1, matching the receiver
environment used for the continuous Opus test.

The stream monitor reports a decoded-PCM gap after 5 seconds plus a 1-second
confirmation window, but it does not tear down a BLE connection that Windows
still reports as connected. It keeps waiting for callbacks, reports recovery,
and lets the user disconnect explicitly if the device does not resume. Initial
MIC startup still requires real PCM and fails cleanly if none arrives. If audio
stops within the first five callbacks, the runtime sends one controlled MIC OFF / MIC
ON recovery after a two-second gap; it never loops that recovery indefinitely.

On Windows, disconnecting a WinRT BLE client cancels pending GATT operations.
Bleak may surface this as WinError 995 (I/O aborted because the thread/application
requested it) or WinError 1223 (operation canceled by the user). During teardown
these messages are expected consequences of the application closing the link,
not proof that the person using Windows manually interrupted it. Cleanup logs
them without replacing the original stream/startup error.

Stream diagnostics include notification count, decoded block count, partial
notification count, the last frame/fragment position, and repeated completed
sequence count. If the last frame remains partially assembled while the BLE
client still reports connected, the notification stream paused in the middle of
a firmware frame. The monitor preserves the session so a later callback can
continue the stream instead of manufacturing a Windows cancel error by
disconnecting it.

## Codec choice

The desktop UI and RingAudioSource default to ADPCM for public SDK 1.4.0.
Saved PCM/Opus UI preferences are migrated to ADPCM. The SDK validates bounded
fragmented IMA blocks and immediately decodes them to mono PCM16 at 16 kHz.
No libopus or host gesture model is required. Explicit legacy codecs remain in
the SDK/CLI only for compatible older firmware, not in the customer UI.

Firmware recognition is started with `26 00` and stopped with `26 01` on the
same BLE connection as audio. Only confirmed `26 06` / `26 07` events drive
application actions; `26 05` intermediate events do not. Existing saved action
names and assignments are preserved. There is no host IMU classification,
threshold tuning, weight loading or model warmup. Raw IMU collection is optional
and off by default; it is independent of gesture recognition.

The supplied DFU was verified separately on 8F56 as `1.2.133.public`, with
ADPCM audio and compact confirmed gestures simultaneously. This project update
is covered by offline protocol/lifecycle/routing tests; hardware acceptance is
performed by the user. Other SDK diagnostic commands depend on firmware support.
