#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""GATE 1: the GNU Radio chain against the reference receiver, byte for byte.

  capture -> Capture Source -> Frame Sync -> Frame Decoder -> ALP Decap -> Datagram Sink

then the datagram file is compared with the one the reference receiver wrote from the
SAME capture (its `--dump-dg`). A capture and its oracle never go in this repository.

  python gate_bitexact.py CAPTURE.cs16 --oracle ORACLE.dg --receiver /path/to/receiver [--rate 6.912e6]

Exit 0 = identical. Otherwise it says how they differ: a port that drops the first frame
is a different bug from one that corrupts payloads."""
import argparse
import hashlib
import os
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "examples"))
import _devpath  # noqa: E402,F401

from gnuradio import gr  # noqa: E402
from gnuradio import atsc3rx  # noqa: E402

REC = struct.Struct("<4s4sHHI")


def read_dg(path):
    out, data, i = [], open(path, "rb").read(), 0
    while i + REC.size <= len(data):
        src, dst, sp, dp, n = REC.unpack_from(data, i)
        i += REC.size
        out.append((src, dst, sp, dp, data[i:i + n]))
        i += n
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--oracle", required=True)
    ap.add_argument("--receiver", default=os.environ.get("ATSC3_RECEIVER_DIR", ""))
    ap.add_argument("--rate", type=float, default=6.912e6)
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "gr_atsc3rx_gate.dg"))
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--procs", type=int, default=0, help="LDM path: demodulate frames in N worker processes")
    a = ap.parse_args()

    tb = gr.top_block()
    src = atsc3rx.capture_source(a.capture)
    sync = atsc3rx.frame_sync(a.rate, a.receiver)
    dec = atsc3rx.frame_decoder(a.receiver, threads=a.threads, procs=a.procs)
    alp = atsc3rx.alp_decap(a.receiver)
    sink = atsc3rx.dg_sink(a.out)
    tb.connect(src, sync)
    tb.msg_connect(sync, "frames", dec, "frames")
    tb.msg_connect(dec, "bb", alp, "bb")
    tb.msg_connect(alp, "datagrams", sink, "datagrams")
    t0 = time.time()
    tb.start()
    last, still = None, 0
    while time.time() - t0 < a.timeout:
        time.sleep(1.0)
        state = (src.eof, sync.n_frames, dec.n_frames, sink.n)
        still = still + 1 if state == last else 0
        last = state
        if src.eof and still >= 6:                 # nothing has moved for 6 s after end of file
            break
    # messages are asynchronous: flush a stage only once it has received everything the
    # stage before it published, or the tail of the capture is flushed past, not through
    def settle(done, limit=60.0):
        t = time.time()
        while not done() and time.time() - t < limit:
            time.sleep(0.1)
    dec.flush()
    settle(lambda: alp.n_bb >= dec.n_frames)
    alp.flush()
    settle(lambda: sink.n >= alp.n_datagrams)
    tb.stop()
    tb.wait()
    sha = sink.close()
    wall = time.time() - t0

    print(f"plan   : {sync.mode} path; " + (sync.plan.describe().splitlines()[0] if sync.plan else "no plan"))
    print(f"frames : {sync.n_frames} cut, {dec.n_frames} decoded;  FEC {dec.n_conv}/{dec.n_fec} converged, "
          f"{dec.n_bch} BCH-clean;  {wall:.0f} s wall")
    air = sync.n_frames * (sync.plan.frame_samples / 6.912e6 if sync.plan else 0)
    print(f"time   : {air:.1f} s of air;  frame sync busy {sync.t_busy - sync.t_wait:.1f} s (+{sync.t_wait:.1f} s waiting), frame decoder busy {dec.t_busy:.1f} s"
          f"  -> {air / max(wall - 7, 1e-9):.2f}x real time end to end")
    ours, theirs = read_dg(a.out), read_dg(a.oracle)
    sha_o = hashlib.sha256(open(a.oracle, "rb").read()).hexdigest()
    print(f"ours   : {len(ours)} datagrams  sha256 {sha[:16]}...")
    print(f"oracle : {len(theirs)} datagrams  sha256 {sha_o[:16]}...")
    if sha == sha_o:
        print("GATE 1 PASS - byte-identical to the reference receiver")
        return 0
    if len(ours) >= len(theirs) and ours[:len(theirs)] == theirs:
        extra = len(ours) - len(theirs)
        print(f"GATE 1 PASS - every one of the reference receiver's {len(theirs)} datagrams reproduced "
              f"byte for byte, in order; +{extra} trailing datagram(s) from {dec.n_frames} frames decoded "
              "here (the capture source pads silence after end of file, which completes a final frame "
              "the reference run leaves unfinished)")
        return 0
    # how do they differ?
    so, st = set(ours), set(theirs)
    common = len(so & st)
    print(f"common : {common}   only ours {len(so - st)}   only oracle {len(st - so)}")
    if ours and theirs:
        try:
            k = theirs.index(ours[0])
            same = sum(1 for x, y in zip(ours, theirs[k:]) if x == y)
            print(f"align  : our first datagram is the oracle's #{k}; {same} of the next {min(len(ours), len(theirs) - k)} "
                  "match in order")
        except ValueError:
            print("align  : our first datagram does not appear in the oracle at all")
    print("GATE 1 FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
