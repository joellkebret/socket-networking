import contextlib
import io
from pathlib import Path
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from udp.client import diagnose
from udp.protocol import Message, decode, encode, reply_to
from udp.server import main, serve

ROOT = Path(__file__).resolve().parents[1]


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.address = self.sock.getsockname()
        self.stop = threading.Event()
        self.errors = []
        self.thread = None

    def start(self, handler=None):
        def target():
            try:
                if handler is None:
                    serve(self.sock, self.stop)
                else:
                    handler()
            except Exception as error:
                self.errors.append(error)
        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(2)
        self.sock.close()
        if self.thread:
            self.assertFalse(self.thread.is_alive())
        self.assertEqual(self.errors, [])

    def test_real_reflector(self):
        self.start()
        results = diagnose(*self.address, count=6, timeout=1, interval=0.005, report=lambda _: None)
        self.assertEqual(len(results.rtts), 6)
        self.assertTrue(all(rtt >= 0 for rtt in results.rtts))
        self.assertIn("Lost: 0 (0.0%)", results.summary())

    def test_blackhole_expires_all_probes(self):
        # Bound UDP port deliberately consumes no packets, avoiding an ICMP refusal.
        results = diagnose(*self.address, count=4, timeout=0.04, interval=0, report=lambda _: None)
        self.assertTrue(all(p.state == "lost" for p in results.probes.values()))
        self.assertIn("Lost: 4 (100.0%)", results.summary())

    def test_controlled_loss_and_reordering(self):
        def rearrange():
            packets = []
            while len(packets) < 4 and not self.stop.is_set():
                try:
                    packets.append(self.sock.recvfrom(1024))
                except socket.timeout:
                    continue
            if len(packets) == 4:
                # One packet is dropped; the others arrive in a different order.
                for index in (2, 0, 0, 1):
                    data, address = packets[index]
                    self.sock.sendto(reply_to(data), address)
        self.start(rearrange)
        result = diagnose(*self.address, count=4, timeout=1, interval=0, report=lambda _: None)
        self.assertEqual(len(result.rtts), 3)
        self.assertEqual(result.out_of_order, 2)
        self.assertEqual(result.duplicates, 1)
        self.assertIn("Lost: 1 (25.0%)", result.summary())

    def test_malformed_and_oversized_datagrams_do_not_stop_server(self):
        self.start()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
            peer.settimeout(1)
            for payload in (b"", b"\xff", b"{}", b"x" * 4096):
                peer.sendto(payload, self.address)
            message = Message(1, "probe", "d" * 32, 17, 987654321)
            peer.sendto(encode(message), self.address)
            reply, address = peer.recvfrom(1024)
            self.assertEqual(address, self.address)
            self.assertEqual(decode(reply).sequence, 17)

    def test_two_clients_have_independent_run_ids(self):
        self.start()
        first = diagnose(*self.address, count=2, interval=0, report=lambda _: None)
        second = diagnose(*self.address, count=2, interval=0, report=lambda _: None)
        self.assertNotEqual(first.run, second.run)
        self.assertEqual((len(first.rtts), len(second.rtts)), (2, 2))

    def test_client_module_entrypoint(self):
        self.start()
        result = subprocess.run(
            [sys.executable, "-m", "udp.client", "--host", self.address[0],
             "--port", str(self.address[1]), "--count", "3", "--interval", "0"],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Sent: 3 | Received: 3 | Lost: 0", result.stdout)


class ServerCliTests(unittest.TestCase):
    def test_invalid_port(self):
        for port in ("0", "65536", "text"):
            with self.subTest(port=port), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as error:
                main(["--port", port])
            self.assertEqual(error.exception.code, 2)

    def test_server_bind_and_cleanup(self):
        with patch("udp.server.socket.socket") as factory, patch("udp.server.serve"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main([]), 0)
            factory.return_value.__enter__.return_value.bind.assert_called_once_with(("127.0.0.1", 9000))
            factory.return_value.__exit__.assert_called_once()

    def test_bind_failure(self):
        with patch("udp.server.socket.socket") as factory, contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as error:
            factory.return_value.__enter__.return_value.bind.side_effect = OSError("in use")
            main([])
        self.assertEqual(error.exception.code, 1)
