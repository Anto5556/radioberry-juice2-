#!/usr/bin/env python3
"""HPSDR protocol-1 stream tester: counts sequence gaps and measures IQ level."""
import math, socket, struct, sys, time

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
SPEED = int(sys.argv[3]) if len(sys.argv) > 3 else 0  # 0=48k 1=96k 2=192k 3=384k
PORT = 1024
IQ_EVERY = 1 << SPEED  # IQ stats on every Nth frame

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
s.bind(("0.0.0.0", 0))
s.settimeout(1.0)

# discovery
s.sendto(bytes([0xEF, 0xFE, 0x02]) + bytes(60), (HOST, PORT))
try:
    d, a = s.recvfrom(2048)
    print(f"discovery reply from {a}: gw={d[9]} board={d[10]} minor={d[21] if len(d)>21 else '?'}")
except socket.timeout:
    print("no discovery reply"); sys.exit(1)

def ep2_frame(seq, c0, c1, c2, c3, c4):
    sub = bytes([0x7F, 0x7F, 0x7F, c0, c1, c2, c3, c4]) + bytes(504)
    sub2 = bytes([0x7F, 0x7F, 0x7F, 0x00, SPEED, 0, 0, 0]) + bytes(504)
    return bytes([0xEF, 0xFE, 0x01, 0x02]) + struct.pack(">I", seq) + sub + sub2

# C0=0: speed, C4 bit layout: 1 receiver ; send config then start
s.sendto(ep2_frame(0, 0x00, SPEED, 0, 0, 0x04), (HOST, PORT))
s.sendto(bytes([0xEF, 0xFE, 0x04, 0x01]) + bytes(60), (HOST, PORT))

last = None; frames = gaps = lost = bad = restarts = 0
sumsq = 0.0; n = 0; peak = 0; tx_seq = 1
t0 = time.time(); next_ep2 = t0
try:
    while time.time() - t0 < SECS:
        now = time.time()
        if now >= next_ep2:  # keep-alive / audio clock frames like an SDR app
            s.sendto(ep2_frame(tx_seq, 0x00, SPEED, 0, 0, 0x04), (HOST, PORT)); tx_seq += 1
            next_ep2 = now + 0.0026
        try:
            d, _ = s.recvfrom(2048)
        except socket.timeout:
            print("timeout waiting for IQ"); continue
        if len(d) != 1032 or d[:4] != b"\xef\xfe\x01\x06":
            bad += 1; continue
        seq = struct.unpack(">I", d[4:8])[0]
        if last is not None and seq != (last + 1) & 0xFFFFFFFF:
            if seq <= last or seq - last > 100000:
                restarts += 1  # FPGA counter restarted (stale frames from a previous run)
            else:
                gaps += 1; lost += seq - last - 1
        last = seq; frames += 1
        if frames % IQ_EVERY:
            continue  # sample IQ stats on a subset so Python keeps up at high rates
        for base in (8, 520):
            if d[base:base+3] != b"\x7f\x7f\x7f":
                bad += 1; continue
            for k in range(63):
                o = base + 8 + k * 8
                i = int.from_bytes(d[o:o+3], "big", signed=True)
                q = int.from_bytes(d[o+3:o+6], "big", signed=True)
                sumsq += i * i + q * q; n += 1
                peak = max(peak, abs(i), abs(q))
finally:
    s.sendto(bytes([0xEF, 0xFE, 0x04, 0x00]) + bytes(60), (HOST, PORT))

el = time.time() - t0
print(f"frames={frames} ({frames/el:.0f}/s) gap_events={gaps} lost={lost} "
      f"loss={100*lost/max(1,frames+lost):.2f}% bad={bad} seq_restarts={restarts}")
if n:
    rms = math.sqrt(sumsq / n)
    print(f"IQ rms={rms:.0f} ({20*math.log10(max(rms,1)/8388607):.1f} dBFS) "
          f"peak={peak} ({20*math.log10(max(peak,1)/8388607):.1f} dBFS)")
