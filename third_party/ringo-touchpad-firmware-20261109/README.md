# dev.touchpad.15 firmware

User-supplied `r1-2.1.6-dev.touchpad.15.zip`, received 2026-11-09.
The ZIP is preserved byte-for-byte. `manifest.json` and `build-info.json` are
exact copies from the archive. The signed BIN remains inside the ZIP; a separate
upload copy is in `outputs/ringo-firmware-20261109-dev15` in the main workspace.

- ZIP SHA-256: `eebe16b0b6b29f8ca2ae39c8e27fd8b9e82242eff7083c203802c86683bce973`
- BIN SHA-256: `6ef922d79c70152cff3529c9657dbfab0f20da7bb23a137ef07376c7bffb9b91`
- BIN size: 646068 bytes
- INFO target: `2.1.6-dev.touchpad.15`
- MCUboot version: `2.1.6+0`

CRC, image hash/size and MCUboot magic were checked. The manifest board, SoC,
load address and slot layout match the prior dev9 package. This archive contains
no SDK implementation, firmware source or changelog. On 2026-11-09, post-flash
INFO returned `2.1.6-dev.touchpad.15.debug`; all 11 starts in the full hardware
matrix received a first packet without the old host workaround. Full lifecycle
acceptance was 10/11 because one explicit stop reported a host-side
`Stream invalidated during inference` error after the device acknowledged STOP.
See [the hardware report](../../outputs/ringo-dev15-verification-20261109/README.md)
for evidence and limits; this is not an all-pass firmware certification.

The existing `ring_mac_sdk` 0.3.0 receiver remains unchanged. See
[`docs/RING_MAC_SDK.md`](../../docs/RING_MAC_SDK.md) for the removed host workaround
and acceptance scope. No firmware was flashed by the agent in this update.
