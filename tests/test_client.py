import contextlib
from dataclasses import replace
import io
import unittest
from unittest.mock import patch

from udp.client import Probe, Results, diagnose, main
from udp.protocol import Message, encode

RUN = "b" * 32


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.results = Results(RUN)
        self.results.probes = {0: Probe(100, 1100), 1: Probe(200, 1200), 2: Probe(300, 1300)}

    def reply(self, index, **changes):
        message = Message(1, "reply", RUN, index, self.results.probes[index].sent_ns)
        return encode(replace(message, **changes))

    def test_rtt_uses_local_monotonic_time(self):
        self.results.accept(self.reply(0), 600)
        self.assertEqual(self.results.rtts, [0.0005])

    def test_out_of_order_replies_are_successful(self):
        for sequence in (2, 0, 1):
            self.results.accept(self.reply(sequence), 700)
        self.assertEqual(len(self.results.rtts), 3)
        self.assertEqual(self.results.out_of_order, 2)

    def test_duplicate_does_not_change_statistics(self):
        self.results.accept(self.reply(0), 500)
        self.assertEqual(self.results.accept(self.reply(0), 600), "duplicate")
        self.assertEqual(len(self.results.rtts), 1)
        self.assertEqual(self.results.duplicates, 1)

    def test_exact_deadline_is_late(self):
        self.assertEqual(self.results.accept(self.reply(0), 1100), "late")
        self.assertEqual(self.results.probes[0].state, "lost")
        self.assertEqual(self.results.late, 1)

    def test_just_before_deadline_is_successful(self):
        self.results.accept(self.reply(0), 1099)
        self.assertEqual(self.results.probes[0].state, "received")

    def test_expiration_is_per_probe(self):
        self.results.expire(1150)
        self.assertEqual([p.state for p in self.results.probes.values()], ["lost", "pending", "pending"])

    def test_noise_cannot_extend_deadlines(self):
        for now in range(400, 1400, 100):
            self.results.accept(b"garbage", now)
        self.assertTrue(all(p.state == "lost" for p in self.results.probes.values()))
        self.assertEqual(self.results.ignored, 10)

    def test_wrong_identity_is_ignored(self):
        for changes in ({"run": "c" * 32}, {"sequence": 99}, {"sent_ns": 101}, {"kind": "probe"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.results.accept(self.reply(0, **changes), 500), "ignored")
        self.assertEqual(self.results.rtts, [])

    def test_timestamp_in_future_is_ignored(self):
        self.assertEqual(self.results.accept(self.reply(0), 99), "ignored")

    def test_summary_includes_loss_and_varied_rtt(self):
        self.results.accept(self.reply(0), 400)
        self.results.accept(self.reply(1), 1000)
        self.results.expire(1400)
        text = self.results.summary()
        self.assertIn("Sent: 3 | Received: 2 | Lost: 1 (33.3%)", text)
        self.assertAlmostEqual(sum(self.results.rtts) / 2, 0.00055)

    def test_summary_latency_format(self):
        self.results.rtts = [1.0, 2.0, 6.0]
        self.assertIn("RTT min/avg/max: 1.000/3.000/6.000 ms", self.results.summary())

    def test_total_loss_has_no_latency(self):
        self.results.expire(1400)
        self.assertIn("Lost: 3 (100.0%)", self.results.summary())
        self.assertIn("RTT min/avg/max: N/A", self.results.summary())


class ConfigurationTests(unittest.TestCase):
    def test_invalid_configuration_before_socket_creation(self):
        for changes in ({"port": 0}, {"port": 65536}, {"count": 0}, {"count": 10001},
                        {"timeout": 0}, {"timeout": -1}, {"timeout": float("nan")},
                        {"timeout": float("inf")}, {"interval": -1}, {"interval": float("inf")}):
            args = dict(host="127.0.0.1", port=9000) | changes
            with self.subTest(changes=changes), patch("udp.client.socket.socket") as factory:
                with self.assertRaises(ValueError):
                    diagnose(**args)
                factory.assert_not_called()

    def test_cli_validation(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["--count", "0"])
        self.assertEqual(error.exception.code, 2)

    def test_network_error_returns_failure(self):
        with patch("udp.client.diagnose", side_effect=OSError("unreachable")), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main([])
        self.assertEqual(error.exception.code, 1)
