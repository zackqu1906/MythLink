"""Strict public-mic IMA ADPCM decoder, matching Android SDK 1.4.0."""
from ring_python_sdk.public_protocol import decode_adpcm


def decode_ima_adpcm_frame(frame: bytes) -> bytes:
    return decode_adpcm(frame) or b""
