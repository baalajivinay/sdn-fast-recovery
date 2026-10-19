#!/usr/bin/env python3
"""
Constant-rate, sequence-numbered UDP probe stream (sender + receiver).

    traffic.py send --dst 10.0.0.2 --pps 5000 --duration 5 --ready-file F
    traffic.py recv --out arrivals.csv --ready-file F

The paper used the kernel packet generator at a 0.05 ms interval. A Python
sender cannot hold that, but at the default 5000 pps (0.2 ms) one lost packet
is 0.2 ms of outage -- fine enough to separate the paper's 3.3 / 15 / 46 ms
BFD results, and the gap-based estimate cross-checks it.

Every packet carries (seq, send_time). Sender, receiver and the experiment
driver share one kernel clock (Mininet hosts are namespaces, not VMs), so
the driver's failure timestamp and per-packet latency are directly
comparable.

Pacing: deadlines are computed from a fixed origin (no drift), the loop
sleeps for the bulk of long intervals and spins for the tail.
The receiver buffers in memory and writes once on SIGTERM so that disk I/O
never lands inside the measurement.
"""

import argparse
import os
import signal
import socket
import struct
import sys
import time

FMT = "!Id"
HDR = struct.calcsize(FMT)


def send(a):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
    sock.setblocking(False)
    pad = b"\x00" * max(0, a.size - HDR)
    interval = 1.0 / a.pps
    total = int(a.duration * a.pps)
    if a.ready_file:
        with open(a.ready_file, "w") as fh:
            fh.write("ready\n")
    origin = time.perf_counter()
    sent = drops = late = 0
    for seq in range(total):
        target = origin + seq * interval
        rem = target - time.perf_counter()
        if rem > 0.0015:
            time.sleep(rem - 0.001)
        while time.perf_counter() < target:
            pass
        if time.perf_counter() - target > 3 * interval:
            late += 1
        try:
            sock.sendto(struct.pack(FMT, seq, time.time()) + pad, (a.dst, a.port))
            sent += 1
        except OSError:
            drops += 1
    sys.stderr.write("sender: attempted=%d sent=%d local_drops=%d late=%d pps=%g\n"
                     % (total, sent, drops, late, a.pps))


_rec = []


def recv(a):
    out = a.out

    def flush(*_):
        with open(out, "w") as fh:
            fh.write("seq,send_time,recv_time\n")
            for s, st, rt in _rec:
                fh.write("%d,%.6f,%.6f\n" % (s, st, rt))
        sys.stderr.write("receiver: recorded %d packets\n" % len(_rec))
        sys.exit(0)

    signal.signal(signal.SIGTERM, flush)
    signal.signal(signal.SIGINT, flush)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 22)
    sock.bind(("0.0.0.0", a.port))
    if a.ready_file:
        with open(a.ready_file, "w") as fh:
            fh.write("ready\n")
    append = _rec.append
    while True:
        try:
            data = sock.recv(2048)
        except InterruptedError:
            continue
        rt = time.time()
        if len(data) >= HDR:
            s, st = struct.unpack_from(FMT, data)
            append((s, st, rt))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--dst", required=True)
    s.add_argument("--port", type=int, default=5001)
    s.add_argument("--pps", type=float, default=5000)
    s.add_argument("--duration", type=float, default=5)
    s.add_argument("--size", type=int, default=64)
    s.add_argument("--ready-file")
    r = sub.add_parser("recv")
    r.add_argument("--port", type=int, default=5001)
    r.add_argument("--out", required=True)
    r.add_argument("--ready-file")
    a = ap.parse_args()
    send(a) if a.cmd == "send" else recv(a)


if __name__ == "__main__":
    main()
