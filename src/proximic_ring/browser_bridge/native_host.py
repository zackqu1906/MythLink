"""Chrome Native Messaging stdio host. stdout is exclusively framed JSON."""
import json
import struct
import sys

from .client import post
from .protocol import MAX_MESSAGE


def read_exact(stream, length):
    chunks = bytearray()
    while len(chunks) < length:
        chunk = stream.read(length - len(chunks))
        if not chunk:
            raise EOFError()
        chunks.extend(chunk)
    return bytes(chunks)


def main():
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            length, = struct.unpack("=I", read_exact(source, 4))
            if not 0 < length <= MAX_MESSAGE:
                return 1
            data = json.loads(read_exact(source, length))
            path = data.pop("path", "")
            if path not in {"/connect", "/exchange"}:
                raise ValueError("invalid_operation")
            result = post(path, data, data.pop("token", ""))
        except EOFError:
            return 0
        except (OSError, ValueError, TypeError, AttributeError):
            result = {"error": "desktop_unavailable"}
        payload = json.dumps(result, separators=(",", ":")).encode()
        try:
            destination.write(struct.pack("=I", len(payload)) + payload)
            destination.flush()
        except BrokenPipeError:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
