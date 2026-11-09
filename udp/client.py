"""Measure UDP round-trip latency and deadline-based packet loss."""

import argparse
from dataclasses import dataclass, field
import math
import select
import socket
import time
import uuid

from udp.protocol import MAX_DATAGRAM, Message, decode, encode


@dataclass
class Probe:
    sent_ns: int
    deadline_ns: int
    state: str = "pending"


@dataclass
class Results:
    run: str
    probes: dict = field(default_factory=dict)
    rtts: list = field(default_factory=list)
    duplicates: int = 0
    late: int = 0
    ignored: int = 0
    out_of_order: int = 0
    highest_reply: int = -1

    def expire(self, now):
        for sequence, probe in self.probes.items():
            if probe.state == "pending" and now >= probe.deadline_ns:
                probe.state = "lost"

    def accept(self, data, now):
        self.expire(now)
        try:
            message = decode(data)
        except ValueError:
            self.ignored += 1
            return "ignored"
        probe = self.probes.get(message.sequence)
        if (message.kind != "reply" or message.run != self.run or probe is None
                or message.sent_ns != probe.sent_ns or now < probe.sent_ns):
            self.ignored += 1
            return "ignored"
        if probe.state == "received":
            self.duplicates += 1
            return "duplicate"
        if probe.state == "lost":
            self.late += 1
            return "late"
        probe.state = "received"
        rtt = (now - probe.sent_ns) / 1_000_000
        self.rtts.append(rtt)
        if message.sequence < self.highest_reply:
            self.out_of_order += 1
        self.highest_reply = max(self.highest_reply, message.sequence)
        return f"reply seq={message.sequence} rtt={rtt:.3f} ms"

    def summary(self):
        sent = len(self.probes)
        received = len(self.rtts)
        lost = sent - received
        loss = 100 * lost / sent if sent else 0.0
        lines = [f"Sent: {sent} | Received: {received} | Lost: {lost} ({loss:.1f}%)"]
        if self.rtts:
            lines.append(f"RTT min/avg/max: {min(self.rtts):.3f}/"
                         f"{sum(self.rtts) / received:.3f}/{max(self.rtts):.3f} ms")
        else:
            lines.append("RTT min/avg/max: N/A")
        lines.append(f"Out-of-order: {self.out_of_order} | Duplicates: {self.duplicates} | "
                     f"Late: {self.late} | Ignored: {self.ignored}")
        return "\n".join(lines)


def diagnose(host, port, count=8, timeout=1.0, interval=0.1, report=print):
    if not 1 <= port <= 65535 or not 1 <= count <= 10000:
        raise ValueError("port must be 1–65535 and count must be 1–10000")
    if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(interval) or interval < 0:
        raise ValueError("timeout must be positive and interval nonnegative; both must be finite")
    results = Results(uuid.uuid4().hex)
    timeout_ns = max(1, int(timeout * 1_000_000_000))
    interval_ns = int(interval * 1_000_000_000)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # Connected UDP filters other source addresses; it does not establish a session.
        sock.connect((host, port))
        sock.setblocking(False)
        next_send = time.monotonic_ns()
        while True:
            now = time.monotonic_ns()
            results.expire(now)
            if len(results.probes) < count and now >= next_send:
                sequence = len(results.probes)
                message = Message(1, "probe", results.run, sequence, now)
                sock.send(encode(message))
                results.probes[sequence] = Probe(now, now + timeout_ns)
                next_send = now + interval_ns
            deadlines = [p.deadline_ns for p in results.probes.values() if p.state == "pending"]
            if len(results.probes) < count:
                deadlines.append(next_send)
            if not deadlines:
                break
            wait = max(0, (min(deadlines) - time.monotonic_ns()) / 1_000_000_000)
            ready, _, _ = select.select([sock], [], [], wait)
            if ready:
                try:
                    data = sock.recv(MAX_DATAGRAM + 1)
                except BlockingIOError:
                    continue
                except OSError as error:
                    if getattr(error, "winerror", None) == 10040:
                        results.ignored += 1
                        continue
                    raise
                report(results.accept(data, time.monotonic_ns()))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=1.0, help="per-probe deadline in seconds")
    parser.add_argument("--interval", type=float, default=0.1, help="seconds between sends")
    args = parser.parse_args(argv)
    try:
        result = diagnose(**vars(args))
    except ValueError as error:
        parser.error(str(error))
    except OSError as error:
        parser.exit(1, f"Network error: {error}\n")
    except KeyboardInterrupt:
        parser.exit(130, "\nInterrupted\n")
    print(result.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
