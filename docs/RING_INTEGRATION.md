# Ringo SDK integration

The current Apple Silicon receiver is [Ring macOS SDK 0.4.0](RING_MAC_SDK.md).
It runs on the existing shared BLE connection. Raw2 waits through temporary
input gaps, and a user can stop a pending start without waiting for a token.
The hardware measurements below were taken with SDK 0.3.0.

## dev.touchpad.15 firmware (2026-11-09)

The new user-supplied ZIP is a signed application firmware package only; it
contains no SDK source update. Its original artifact and metadata are retained
under `third_party/ringo-touchpad-firmware-20261109`, with upload files under
`outputs/ringo-firmware-20261109-dev15`. See [the firmware update record](RING_MAC_SDK.md).
The user's reported firmware fix replaces our dev9 startup-order workaround:
Raw2 no longer pauses/resumes Swipe at startup or resets Swipe after stop/failure.
MIC and gesture processor ownership, provider control scheduling, timeout
diagnostics and macOS BLE cancellation fixes remain in place. The original
provider package is unchanged. Post-flash INFO returned
`2.1.6-dev.touchpad.15.debug`. The 2026-11-09 hardware matrix received the first
packet in all 11 starts (27.50–179.15 ms), including all three startup orders
and two product `RingAudioSource` cycles. Gesture and MIC data continued during
Raw2 operation and after Raw2 stopped, without the old Swipe pause/reset commands.
Full lifecycle acceptance was 10/11: one explicit stop received a device STOP ACK
but reported `Stream invalidated during inference` in the host receiver. An
earlier aborted matrix also recorded that stop-time error. It remains visible
and unfixed; this is not an all-pass report. No OS mouse actions were emitted by
the probe. See [the hardware report](../outputs/ringo-dev15-verification-20261109/README.md).

## 2026-11-05 delivery: Raw2 product integration

The provider `ring_mac_sdk` 0.3.0 package introduced the Raw2 integration.
Its original wheel, documentation, examples, calibration
utility and dev9 firmware are retained under
`third_party/ringo-touchpad-delivery-20261105`. See
[the update and feature inventory](RING_MAC_SDK.md) for provenance, standalone
validation and dependencies. On macOS the provider Transport owns BLE connection,
NUS/INFO validation, notifications, and all control writes (Bleak 1.1.1).
Apple Silicon touchpad uses `ring_python_sdk.touchpad.raw2`; audio and firmware
gestures share that validated connection through compatibility processors. Mouse settings expose independent
single, double, right-click and vertical scroll controls. Other sensor APIs and
unsupported platforms retain their existing compatibility paths.

## Connection controls (2026-11-09)

The home card, other pages' device header, and menu-bar connection action share
`AppController.connectionInfo`. Connecting exposes **取消连接**; cancellation
and disconnection disable the action until cleanup finishes. A cancelled attempt
ends at **已取消连接**, retains the selected device, and never triggers automatic
reconnect or a connection-failure alert. A real failure exposes **重试连接** and
its reason inside the device card, including at the smaller window size. BLE
readiness remains separate from voice/model readiness: a verified link shows
**已连接 · 识别组件准备中** and can still be disconnected during preparation.

Cancellation covers both a selected connection waiting for scanner cleanup and
the actual device-resolution/connection/NUS/INFO task. `RingAudioSource` observes
the runtime's cancellation event, cancels that task on the BLE loop, and awaits
the SDK's existing cleanup without cancelling it a second time. The original
provider SDK and its timeouts are unchanged. Existing connection-identity checks
reject late ready/finished callbacks from a previous attempt.

On macOS, picker scans and Ring sessions share a process-lifetime daemon BLE
loop (`proximic_ring.ble_runtime`). Finishing/cancelling one session does not
close that loop: CoreBluetooth can still deliver queued delegate callbacks.
The 2026-11-09 crash logs showed `didConnectPeripheral` scheduling onto a closed
per-attempt loop and aborting via PyObjC. The pinned Bleak 1.1.1 also returns
early from `disconnect()` for a peripheral still connecting. The SDK adapter's
`CancellableMacClient` cancels that native request and awaits its disconnect ACK,
using the provider's existing close timeout. If CoreBluetooth omits that ACK,
the adapter retires the old central and removes its session callback before a
retry obtains a fresh central; delayed callbacks still target the live BLE loop.
The provider package is unchanged.
Remembered-device lookup supports both Bleak 1.1.1's synchronous readiness and
the async readiness API in 3.x. Windows retains its own COM-initialized worker.

`test_macos_ble_lifecycle.py` exercises the installed CoreBluetooth delegate
implementation with delayed callbacks after 20 scan/session worker lifetimes,
and native-connect cancellation with a late success before the disconnect ACK.

Validation on 2026-11-09: 229 targeted lifecycle, UI, SDK, audio, touchpad and
gesture-source checks passed. An isolated hardware process using the product's
`RingAudioSource` and installed Bleak/PyObjC completed 24 connection cancellations
and 8 scan cancellations against Ringo8F56, then successfully reconnected and
read INFO (`2.1.6-dev.touchpad.9.debug`). All operations used one running loop;
there were no loop callback errors or native aborts. Two immediate cancellations
reached the existing 5-second close timeout; retiring their centrals allowed
subsequent attempts to proceed. Hardware mouse/gesture actions were not emitted.

An additional broader firmware-gesture test run had 77 passes and one failure
in `test_gesture_conversion_and_undo_use_existing_availability` (missing manual
voice-mode switch actions); that failure also reproduced on its own. Its voice
routing path was not changed by this Bluetooth fix and remains a separate issue.

`test_ring_connection_cancel.py` exercises the real session/provider with a fake
BLE client at connect, notification subscription and INFO waits, plus discovery
cancellation. `test_ui_connection_states.py` exercises actual QML clicks,
cancellation/retry races, voice-preparation separation and both window sizes.
These checks do not replace physical-device validation.

## Existing product integration

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
