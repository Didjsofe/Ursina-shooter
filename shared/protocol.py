"""Newline-delimited UTF-8 JSON. Each TCP frame is one JSON object + LF."""
import json

MAX_FRAME = 64 * 1024

def encode(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")

def extract_frames(buffer: bytes):
    """Return complete decoded frames and the unconsumed byte tail."""
    frames = []
    while b"\n" in buffer:
        raw, buffer = buffer.split(b"\n", 1)
        if len(raw) > MAX_FRAME:
            raise ValueError("frame too large")
        if raw:
            frames.append(json.loads(raw.decode("utf-8")))
    if len(buffer) > MAX_FRAME:
        raise ValueError("frame too large")
    return frames, buffer
