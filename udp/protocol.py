"""Version 1 of the socket-networking datagram protocol."""

import json
import re
from dataclasses import asdict, dataclass

MAX_DATAGRAM = 512
MAX_INTEGER = 2**63 - 1
FIELDS = {"version", "kind", "run", "sequence", "sent_ns"}


@dataclass(frozen=True)
class Message:
    version: int
    kind: str
    run: str
    sequence: int
    sent_ns: int


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def decode(data):
    """Validate an entire datagram. Invalid messages raise ValueError."""
    if len(data) > MAX_DATAGRAM:
        raise ValueError("datagram exceeds 512 bytes")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ValueError("invalid JSON datagram") from error
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("unexpected fields")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported protocol version")
    if value["kind"] not in ("probe", "reply"):
        raise ValueError("unknown message kind")
    if not isinstance(value["run"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["run"]):
        raise ValueError("invalid run ID")
    for field in ("sequence", "sent_ns"):
        if type(value[field]) is not int or not 0 <= value[field] <= MAX_INTEGER:
            raise ValueError(f"invalid {field}")
    return Message(**value)


def encode(message):
    data = json.dumps(asdict(message), separators=(",", ":")).encode("utf-8")
    decode(data)
    return data


def reply_to(data):
    """Silently discard malformed packets and replies; echo valid probes."""
    try:
        message = decode(data)
    except ValueError:
        return None
    if message.kind != "probe":
        return None
    return encode(Message(1, "reply", message.run, message.sequence, message.sent_ns))
