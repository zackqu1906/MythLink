"""Device families used by discovery, reconnect, and audio defaults."""

import re


def ring_family(name: str, pnp_id: bytes | None = None) -> str:
    # R1-PC prioritizes the advertised DIS product ID over the display name.
    if pnp_id == bytes.fromhex("02 39 3a 31 52"):
        return "R1"
    if pnp_id is None and (
        re.fullmatch(r"(?:RR_|R1)[A-Z0-9-]{4}", name, re.IGNORECASE | re.ASCII)
        or (name.endswith("的Ringo") and 6 < len(name) and len(name.encode("utf-8")) <= 29)
    ):
        return "R1"
    return "Ringo" if "ringo" in name.casefold() else ""
