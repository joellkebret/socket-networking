import json
import unittest
from dataclasses import asdict, replace

from udp.protocol import MAX_DATAGRAM, Message, decode, encode, reply_to

BASE = Message(1, "probe", "a" * 32, 0, 123456789)


class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        for kind in ("probe", "reply"):
            for sequence in (0, 42, 2**63 - 1):
                message = replace(BASE, kind=kind, sequence=sequence)
                self.assertEqual(decode(encode(message)), message)

    def test_reflector_preserves_identity_and_timestamp(self):
        reply = decode(reply_to(encode(BASE)))
        self.assertEqual(reply, replace(BASE, kind="reply"))
        self.assertLessEqual(len(encode(reply)), MAX_DATAGRAM)

    def test_reflector_does_not_reply_to_replies(self):
        self.assertIsNone(reply_to(encode(replace(BASE, kind="reply"))))

    def test_duplicate_keys_rejected(self):
        payload = encode(BASE).replace(b'"version":1', b'"version":1,"version":1')
        with self.assertRaises(ValueError):
            decode(payload)

    def test_missing_field(self):
        for key in asdict(BASE):
            value = asdict(BASE)
            del value[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                decode(json.dumps(value).encode())

    def test_extra_field(self):
        value = asdict(BASE) | {"payload": "unexpected"}
        with self.assertRaises(ValueError):
            decode(json.dumps(value).encode())

    def test_json_whitespace_is_allowed(self):
        self.assertEqual(decode(json.dumps(asdict(BASE), indent=2).encode()), BASE)


def invalid_field(field, value):
    def test(self):
        data = json.dumps(asdict(BASE) | {field: value}).encode()
        with self.assertRaises(ValueError):
            decode(data)
        self.assertIsNone(reply_to(data))
    return test


for name, field, value in [
    ("unknown_version", "version", 2), ("boolean_version", "version", True),
    ("string_version", "version", "1"), ("unknown_kind", "kind", "hello"),
    ("null_kind", "kind", None), ("list_kind", "kind", []),
    ("short_run", "run", "a"), ("uppercase_run", "run", "A" * 32),
    ("non_hex_run", "run", "z" * 32), ("numeric_run", "run", 123),
    ("negative_sequence", "sequence", -1), ("boolean_sequence", "sequence", False),
    ("fractional_sequence", "sequence", 1.5), ("overflow_sequence", "sequence", 2**63),
    ("negative_timestamp", "sent_ns", -1), ("string_timestamp", "sent_ns", "123"),
    ("boolean_timestamp", "sent_ns", True), ("overflow_timestamp", "sent_ns", 2**63),
]:
    setattr(ProtocolTests, f"test_{name}", invalid_field(field, value))


def malformed_packet(data):
    def test(self):
        with self.assertRaises(ValueError):
            decode(data)
        self.assertIsNone(reply_to(data))
    return test


for name, data in {
    "empty": b"", "utf8": b"\xff", "array": b"[]", "null": b"null",
    "truncated": b'{"version":', "oversized": b" " * 513,
    "trailing_junk": encode(BASE) + b"junk",
}.items():
    setattr(ProtocolTests, f"test_malformed_{name}", malformed_packet(data))
