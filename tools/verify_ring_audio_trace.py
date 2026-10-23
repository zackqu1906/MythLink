"""Audit raw MIC notifications without importing the Ring SDK.

Use the project's Python 3.11 runtime (stdlib audioop supplies an independent
IMA ADPCM decoder). Old logs without raw notifications cannot prove framing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import warnings


def verify(row: dict) -> dict:
    result = {key: row.get(key) for key in ("event", "capture", "sequence")}
    packets = row.get("raw_notifications_hex")
    if not packets:
        return {**result, "status": "missing_raw_notifications"}
    try:
        parts, expected_count = {}, None
        for value in packets:
            packet = bytes.fromhex(value)
            if not 12 < len(packet) <= 818 or packet[:2] != b"\x20\x03":
                raise ValueError("invalid notification header/size")
            seq, index, count, uptime = struct.unpack_from("<HHHI", packet, 2)
            if (seq, uptime) != (row["sequence"], row["uptime_ms"]):
                raise ValueError("notification sequence/uptime differs from decoded frame")
            if not 1 <= count <= 806 or index >= count:
                raise ValueError("invalid fragment index/count")
            if expected_count is not None and expected_count != count:
                raise ValueError("conflicting fragment counts")
            expected_count = count
            payload = packet[12:]
            if index in parts and parts[index] != payload:
                raise ValueError("conflicting duplicate fragment")
            parts[index] = payload
        result.update(raw_packet_count=len(packets), fragments=len(parts))
        if set(parts) != set(range(expected_count)):
            return {**result, "status": "incomplete_raw_trace"}
        block = b"".join(parts[i] for i in range(expected_count))
        if not 6 <= len(block) <= 806:
            raise ValueError("invalid assembled block size")
        result["raw_matches_encoded"] = (
            block == bytes.fromhex(row["encoded_hex"])
            and hashlib.sha256(block).hexdigest() == row["encoded_sha256"])
        predictor, index, reserved, count = struct.unpack_from("<hBBH", block)
        if index > 88 or reserved or not 1 <= count <= 1600 or len(block) != 6 + count // 2:
            raise ValueError("invalid ADPCM block header")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                import audioop
        except ImportError:
            return {**result, "status": "independent_decoder_unavailable"}
        # Wire uses low nibble first; audioop uses high nibble first. The wire
        # predictor is the first sample, followed by count-1 encoded samples.
        swapped = bytes(((b & 15) << 4) | (b >> 4) for b in block[6:])
        decoded, _ = audioop.adpcm2lin(swapped, 2, (predictor, index))
        pcm = struct.pack("<h", predictor) + decoded[:2 * (count - 1)]
        result["independent_pcm_sha256"] = hashlib.sha256(pcm).hexdigest()
        result["pcm_matches_sdk"] = result["independent_pcm_sha256"] == row["pcm_sha256"]
        result["pcm_all_zero"] = not any(pcm)
        result["status"] = ("verified" if result["raw_matches_encoded"]
                            and result["pcm_matches_sdk"] else "mismatch")
    except (ValueError, KeyError, TypeError, struct.error) as exc:
        result.update(status="invalid_evidence", error=str(exc))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    results = []
    marker = "[RING AUDIO HEALTH] "
    for path in args.logs:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if marker not in line:
                continue
            try:
                row = json.loads(line.split(marker, 1)[1])
                if "encoded_hex" not in row:
                    continue
                results.append({"log": str(path), "line": number, **verify(row)})
            except json.JSONDecodeError as exc:
                results.append({"log": str(path), "line": number,
                                "status": "invalid_evidence", "error": str(exc)})
    text = json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")
    if any(r["status"] in {"mismatch", "invalid_evidence"} for r in results):
        return 1
    return 0 if any(r["status"] == "verified" for r in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
