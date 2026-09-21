#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""SAME-SAMPLES stress test: this GNU Radio chain against the reference receiver, live, side by side.

One radio cannot feed two receivers, and two runs an hour apart are two different channels. So GNU
Radio owns the radio and the samples are quantised ONCE to 16-bit I/Q; from there

    Soapy -> int16 I/Q --+--> back to complex -> Frame Sync -> Frame Decoder -> ALP Decap -> datagram file
                         +--> growing .cs16 file ==> reference receiver  --capture FILE --realtime --dump-dg

Both decoders see the identical sample values, the reference a few seconds behind. Every dropout can
then be laid at the door of the air (both lost it) or of the code (one lost it).

  stress_ab.py --soapy "driver=..." --freq HZ [--antenna NAME] [--gain ELEMENT=VALUE ...]
               --out DIR --seconds 3600 --reference-python PY

The IQ file grows ~100 GB an hour and is deleted at the end unless --keep-iq. Nothing in DIR belongs
in a repository. If the radio is shared, RXTUNE_LOCK (see gr-rxtune) is honoured.
Afterwards: stress_ab_report.py DIR"""
import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "examples"))
import _devpath  # noqa: E402,F401

import pmt  # noqa: E402
from gnuradio import atsc3rx, blocks, gr, soapy  # noqa: E402


class snr_watch(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name="snr_watch", in_sig=None, out_sig=None)
        self.last = self.lo = None
        self.message_port_register_in(pmt.intern("dial"))
        self.set_msg_handler(pmt.intern("dial"), self.on_dial)

    def on_dial(self, msg):
        self.last = pmt.to_double(pmt.cdr(msg))
        self.lo = self.last if self.lo is None else min(self.lo, self.last)

    def take_min(self):
        lo, self.lo = self.lo, None
        return lo


def site_lock(owner):
    if not os.environ.get("RXTUNE_LOCK"):
        return contextlib.nullcontext(None)
    from rxtune import lock
    return lock.from_env(owner=owner, priority=60)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--soapy", required=True)
    ap.add_argument("--freq", type=float, required=True)
    ap.add_argument("--antenna")
    ap.add_argument("--gain", nargs="*", default=[], metavar="ELEMENT=VALUE")
    ap.add_argument("--rate", type=float, default=6.912e6)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=3600.0)
    ap.add_argument("--receiver", default=os.environ.get("ATSC3_RECEIVER_DIR", ""))
    ap.add_argument("--reference-python", default=os.environ.get("ATSC3_PYTHON") or sys.executable)
    ap.add_argument("--head-start", type=float, default=12.0, help="seconds of file before the reference starts")
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--every", type=float, default=10.0)
    ap.add_argument("--keep-iq", action="store_true")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    iq = os.path.join(out, "air.cs16")
    for f in ("air.cs16", "ours.dg", "ref.dg", "ref.json", "ref.log", "ours.jsonl", "done.json"):
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(out, f))

    ref = None
    with site_lock("atsc3rx-stress") as lk:
        tb = gr.top_block()
        src = soapy.source(a.soapy, "fc32", 1, "", "", [""], [""])
        src.set_sample_rate(0, a.rate)
        src.set_bandwidth(0, 8e6)
        src.set_frequency(0, a.freq)
        src.set_gain_mode(0, False)
        if a.antenna:
            src.set_antenna(0, a.antenna)
        for item in a.gain:
            name, val = item.split("=")
            src.set_gain(0, name, float(val))
        src.set_min_output_buffer(1 << 23)
        to_i16 = blocks.complex_to_interleaved_short(False, 32767.0)       # the ONE quantisation both sides share
        back = blocks.interleaved_short_to_complex(False, False, 32768.0)
        fsink = blocks.file_sink(gr.sizeof_short, iq, False)
        fsink.set_unbuffered(False)
        sync = atsc3rx.frame_sync(a.rate, a.receiver, drop_when_behind=True, settle_sec=0.0)
        dec = atsc3rx.frame_decoder(a.receiver, threads=a.threads, procs=a.procs)
        alp = atsc3rx.alp_decap(a.receiver)
        dgs = atsc3rx.dg_sink(os.path.join(out, "ours.dg"))
        snr = snr_watch()
        # the radio's settling seconds are dropped BEFORE the split, so neither side ever sees them
        skip = blocks.skiphead(gr.sizeof_gr_complex, int(2.0 * a.rate))
        tb.connect(src, skip, to_i16)
        tb.connect(to_i16, fsink)
        tb.connect(to_i16, back, sync)
        tb.msg_connect(sync, "frames", dec, "frames")
        tb.msg_connect(sync, "plan", dec, "plan")
        tb.msg_connect(dec, "feedback", sync, "feedback")
        tb.msg_connect(dec, "bb", alp, "bb")
        tb.msg_connect(dec, "dial", snr, "dial")
        tb.msg_connect(alp, "datagrams", dgs, "datagrams")

        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, lambda *_: stop.set())
        log = open(os.path.join(out, "ours.jsonl"), "a", buffering=1)
        t0 = time.time()
        tb.start()
        nxt = t0 + a.every
        why = "interrupted"
        while not stop.is_set():
            time.sleep(0.5)
            now = time.time()
            if lk is not None:
                lk.heartbeat()
                y = lk.should_yield()
                if y:
                    why = f"yielded the radio: {y}"
                    break
            if ref is None and now - t0 >= a.head_start and os.path.exists(iq):
                ref = subprocess.Popen(
                    [a.reference_python, "-u", "-m", "atsc3", "watch", "--capture", iq, "--fmt", "cs16",
                     "--rate", str(a.rate), "--realtime", "--player", "none", "--report", str(a.every),
                     "--dump-dg", os.path.join(out, "ref.dg"), "--json", os.path.join(out, "ref.json")],
                    cwd=a.receiver, stderr=subprocess.STDOUT, stdout=open(os.path.join(out, "ref.log"), "ab"))
            if now >= nxt:
                nxt += a.every
                rec = {"t": round(now - t0, 1), "wall": round(now, 1), "frames": dec.n_frames, "fec": dec.n_fec,
                       "bch": dec.n_bch, "conv": dec.n_conv, "reacq": sync.n_reacquire, "breaks": sync.n_breaks,
                       "dropped": sync.n_dropped, "snr": snr.last, "snr_min": snr.take_min(),
                       "datagrams": alp.n_datagrams, "iq_gb": round(os.path.getsize(iq) / 1e9, 2),
                       "ref_alive": ref is not None and ref.poll() is None}
                log.write(json.dumps(rec) + "\n")
                print(json.dumps(rec), flush=True)
            if now - t0 >= a.seconds:
                why = f"{a.seconds:.0f} s elapsed"
                break
        tb.stop()
        tb.wait()
        sha = dgs.close()
        wall = time.time() - t0
    # the radio is closed and released. The reference now reads to the end of the file and sums up.
    ref_rc = None
    if ref is not None:
        try:
            ref_rc = ref.wait(timeout=300)
        except subprocess.TimeoutExpired:
            ref.terminate()
            ref_rc = "terminated after 300 s"
    done = {"why": why, "wall_s": round(wall, 1), "ours": {"frames": dec.n_frames, "fec": dec.n_fec, "bch": dec.n_bch,
            "reacquisitions": sync.n_reacquire, "continuity_breaks": sync.n_breaks, "frames_dropped": sync.n_dropped,
            "datagrams": alp.n_datagrams, "dg_sha256": sha}, "ref_rc": ref_rc,
            "iq_bytes": os.path.getsize(iq) if os.path.exists(iq) else 0}
    if not a.keep_iq:
        for _ in range(30):
            try:
                os.remove(iq)
                break
            except OSError:
                time.sleep(1.0)
    with open(os.path.join(out, "done.json"), "w") as fh:
        json.dump(done, fh, indent=1)
    print(json.dumps(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
