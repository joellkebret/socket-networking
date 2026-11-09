"""Stateless UDP reflector for socket-networking."""

import argparse
import socket

from udp.protocol import MAX_DATAGRAM, reply_to


def serve(sock, stop=None):
    """Run until interrupted, or until an optional threading.Event is set."""
    sock.settimeout(0.1)
    while stop is None or not stop.is_set():
        try:
            data, address = sock.recvfrom(MAX_DATAGRAM + 1)
        except socket.timeout:
            continue
        except OSError as error:
            # Windows reports oversized UDP datagrams instead of truncating them.
            if getattr(error, "winerror", None) == 10040:
                continue
            raise
        response = reply_to(data)
        if response is not None:
            try:
                sock.sendto(response, address)
            except OSError:
                # An unreachable peer should not stop service to other clients.
                continue


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="IPv4 bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((args.host, args.port))
            print(f"socket-networking listening on {args.host}:{args.port}", flush=True)
            serve(sock)
    except KeyboardInterrupt:
        print("\nServer stopped")
    except OSError as error:
        parser.exit(1, f"Network error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
