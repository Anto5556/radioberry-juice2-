#!/usr/bin/env python3
"""Capture IQ from a protocol-1 (HermesLite-2 style) radio and print basic stats.

usage: p1_iqcapture.py [--host 127.0.0.1] [--freq 7074000] [--lna 99] [--frames 2000] out.npy

--lna sets the raw HL2 LNA register (C0=0x14, value 0..60).
Leave at 99 to not send it (gateware default).
"""
import argparse, socket, struct, time
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--freq", type=int, default=7074000)
ap.add_argument("--lna", type=int, default=99)
ap.add_argument("--frames", type=int, default=2000)
a = ap.parse_args()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
s.bind(("0.0.0.0", 0)); s.settimeout(1)

def ep2(seq, c_a, c_b):
    subs = b"".join(bytes([0x7F, 0x7F, 0x7F]) + bytes(c) + bytes(504) for c in (c_a, c_b))
    return b"\xef\xfe\x01\x02" + struct.pack(">I", seq) + subs

cfg = [0x00, 0x00, 0, 0, 0x04]                       # 48 kHz, 1 RX, duplex
rx1 = [0x04] + list(struct.pack(">I", a.freq))        # RX1 NCO frequency
lna = [0x14, 0, 0, 0, 0x40 | a.lna] if a.lna != 99 else cfg
dst = (a.host, 1024)
seq = 0
for c in (lna, rx1, lna):
    s.sendto(ep2(seq, cfg, c), dst); seq += 1
s.sendto(b"\xef\xfe\x04\x01" + bytes(60), dst)       # start

iq, seqs, nxt = [], [], 0.0
while len(seqs) < a.frames:
    if time.time() > nxt:                             # keep sending C&C like an SDR app
        s.sendto(ep2(seq, cfg, rx1 if seq % 2 else lna), dst); seq += 1
        nxt = time.time() + 0.0026
    try:
        d, _ = s.recvfrom(2048)
    except socket.timeout:
        print("timeout"); continue
    if len(d) != 1032 or d[3] != 6:
        continue
    seqs.append(struct.unpack(">I", d[4:8])[0])
    for b in (8, 520):
        blk = np.frombuffer(d[b + 8:b + 512], dtype=np.uint8).reshape(63, 8).astype(np.int32)
        i = blk[:, 0] << 16 | blk[:, 1] << 8 | blk[:, 2]; i = np.where(i >= 1 << 23, i - (1 << 24), i)
        q = blk[:, 3] << 16 | blk[:, 4] << 8 | blk[:, 5]; q = np.where(q >= 1 << 23, q - (1 << 24), q)
        iq.append(i + 1j * q)
s.sendto(b"\xef\xfe\x04\x00" + bytes(60), dst)       # stop

x = np.concatenate(iq); np.save(a.out, x)
seqs = np.array(seqs, dtype=np.int64)
print("lost frames:", int((np.diff(seqs) - 1).sum()))
print("rms %.1f dBFS" % (20 * np.log10(np.sqrt(np.mean(np.abs(x) ** 2)) / 8388607)))
print("Q nonzero fraction: %.3f" % np.mean(x.imag != 0))
print("samples with low 12 bits zero (garbage indicator): %.3f" % np.mean((x.real.astype(np.int64) & 0xFFF) == 0))
seg = x[:32768] - x[:32768].mean(); w = np.hanning(len(seg))
sp = 20 * np.log10(np.abs(np.fft.fft(seg * w)) / (8388607 * w.sum()) + 1e-12)
print("noise floor (median) %.1f dBFS/bin, strongest %.1f dBFS/bin" % (np.median(sp), sp.max()))
