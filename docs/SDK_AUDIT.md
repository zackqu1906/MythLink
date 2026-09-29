# Supplied ring-python-sdk audit

The supplied `ring-python-sdk-main.zip` was inspected before this integration.
The important MIC findings are:

1. `RingSession.mic_on(encode_name, on_pcm=...)` is the intended high-level API.
2. BLE scanning/connection and Nordic UART Service characteristic discovery are
   already implemented by the SDK; ProxiMic should not duplicate them.
3. `AudioProcessor` assembles MIC fragments and supports raw PCM, IMA ADPCM, and
   Opus packets.
4. `AudioProcessor._accept_pcm(...)` invokes the real-time `on_pcm` callback and
   writes the same decoded stream to WAV.
5. SDK constants define 16 kHz, one channel, 2-byte samples.  This is exactly the
   ProxiMic detector's required front-end format.
6. Opus decoding produces 320 samples per Opus frame and 5 frames per block.

Because the SDK already solves the hardware protocol, the previous generic
`ble.py`, `serial.py`, and `udp.py` adapters were removed from ProxiMic.

## Public SDK 1.4.0 update (2026-09-28)

The application now defaults to ADPCM everywhere (UI, runtime, CLI and dataset
capture). Its decoder validates the public firmware header, bounds fragments to
one 806-byte block and writes decoded PCM immediately. Default audio has no
libopus dependency. Saved codec preferences migrate to ADPCM.

Firmware-confirmed `26 06` / compact `26 07` events drive all gesture actions;
intermediate classifications do not. Host gesture models/parameters/weights are
removed. Raw IMU logging is optional and off by default. Quaternion frames are
available through `RingSession.quaternion_on(on_frame=...)` and
`quaternion_off()`, independently of firmware gestures; raw IMU and quaternion
capture share a stream and cannot run together.
