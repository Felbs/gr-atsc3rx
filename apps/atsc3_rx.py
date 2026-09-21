#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Receive ATSC 3.0: a capture or a SoapySDR radio in, IP datagrams out.

  atsc3_rx.py --capture FILE.cs16 [--dump-dg OUT.dg]
  atsc3_rx.py --soapy "driver=..." --freq HZ [--antenna NAME] [--gain ELEMENT=VALUE ...]
              [--seconds N] [--udp] [--udp-host HOST] [--dump-dg OUT.dg]

Prints one status line a second: frames cut / decoded / dropped, FEC blocks converged and
BCH-clean, SNR off the known dummy cells, datagrams out. `--udp` puts the datagrams back on
the loopback interface at the multicast addresses they were broadcast with (TTL 0).

It stops at IP datagrams and never decrypts anything.
Python phase: needs the reference receiver (--receiver or ATSC3_RECEIVER_DIR).
If the radio is shared, RXTUNE_LOCK (see gr-rxtune) names a site lock module and is honoured."""
import argparse
import contextlib
import os
import signal
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "examples"))
try:
    from gnuradio import atsc3rx
except ImportError:
    import _devpath  # noqa: F401
    from gnuradio import atsc3rx

import pmt  # noqa: E402
from gnuradio import gr  # noqa: E402


class watcher(gr.basic_block):
    """Keeps the last plan and the last quality report."""

    def __init__(self):
        gr.basic_block.__init__(self, name="watcher", in_sig=None, out_sig=None)
        self.plan, self.q = None, {}
        self.snr = []
        for port, fn in (("plan", self.on_plan), ("quality", self.on_quality)):
            self.message_port_register_in(pmt.intern(port))
            self.set_msg_handler(pmt.intern(port), fn)

    def on_plan(self, msg):
        self.plan = pmt.to_python(msg)

    def on_quality(self, msg):
        self.q = pmt.to_python(msg)
        if "snr_db" in self.q:
            self.snr.append(self.q["snr_db"])


def site_lock(owner):
    """The radio may be shared. gr-rxtune defines the convention; use it if it is there."""
    if not os.environ.get("RXTUNE_LOCK"):
        return contextlib.nullcontext(None)
    try:
        from rxtune import lock
    except ImportError:
        sys.exit("RXTUNE_LOCK is set but the rxtune package is not importable: refusing to open a shared radio unlocked")
    return lock.from_env(owner=owner, priority=60)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture")
    ap.add_argument("--soapy")
    ap.add_argument("--freq", type=float)
    ap.add_argument("--antenna")
    ap.add_argument("--gain", nargs="*", default=[], metavar="ELEMENT=VALUE")
    ap.add_argument("--bandwidth", type=float, default=8e6)
    ap.add_argument("--rate", type=float, default=6.912e6)
    ap.add_argument("--seconds", type=float, default=0.0, help="radio: stop after this long (0 = until Ctrl-C)")
    ap.add_argument("--receiver", default=os.environ.get("ATSC3_RECEIVER_DIR", ""))
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--procs", type=int, default=4, help="worker processes for CTI/LDM multiplexes (0 = none)")
    ap.add_argument("--udp", action="store_true", help="send datagrams to their own multicast groups, TTL 0")
    ap.add_argument("--udp-host", default="", help="send every datagram to this host instead")
    ap.add_argument("--dump-dg", default="")
    ap.add_argument("--settle", type=float, default=2.0, help="radio: discard this many seconds after the stream starts")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if not (a.capture or (a.soapy and a.freq)):
        ap.error("give --capture FILE, or --soapy ARGS with --freq HZ")
    live = not a.capture

    with site_lock("atsc3rx") as lk:
        tb = gr.top_block()
        if live:
            from gnuradio import soapy
            src = soapy.source(a.soapy, "fc32", 1, "", "", [""], [""])
            src.set_sample_rate(0, a.rate)
            src.set_bandwidth(0, a.bandwidth)
            src.set_frequency(0, a.freq)
            src.set_gain_mode(0, False)
            if a.antenna:
                src.set_antenna(0, a.antenna)
            for item in a.gain:
                name, val = item.split("=")
                src.set_gain(0, name, float(val))
            # Start-up is the hungry moment: acquisition and the decoder's table building run
            # while the radio keeps delivering. A second of buffer after the source rides it out;
            # without it the driver overflows and the first seconds of frames are lost.
            src.set_min_output_buffer(1 << 23)
        else:
            src = atsc3rx.capture_source(a.capture)
        sync = atsc3rx.frame_sync(a.rate, a.receiver, drop_when_behind=live, settle_sec=a.settle if live else 0.0)
        dec = atsc3rx.frame_decoder(a.receiver, threads=a.threads, procs=a.procs)
        alp = atsc3rx.alp_decap(a.receiver)
        watch = watcher()
        tb.connect(src, sync)
        tb.msg_connect(sync, "frames", dec, "frames")
        tb.msg_connect(sync, "plan", dec, "plan")
        tb.msg_connect(sync, "plan", watch, "plan")
        tb.msg_connect(dec, "bb", alp, "bb")
        tb.msg_connect(dec, "feedback", sync, "feedback")
        tb.msg_connect(dec, "quality", watch, "quality")
        sinks = []
        if a.udp or a.udp_host:
            sinks.append(atsc3rx.dg_udp_sink(host=a.udp_host))
        if a.dump_dg:
            sinks.append(atsc3rx.dg_sink(a.dump_dg))
        for s in sinks:
            tb.msg_connect(alp, "datagrams", s, "datagrams")

        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, lambda *_: stop.set())
        t0 = time.time()
        tb.start()
        said_plan, last, still, why = False, None, 0, "end of input"
        while not stop.is_set():
            time.sleep(1.0)
            if lk is not None:
                lk.heartbeat()
                y = lk.should_yield()
                if y:
                    why = f"yielded the radio: {y}"
                    break
            if watch.plan and not said_plan:
                said_plan = True
                print(watch.plan["describe"])
                print(f"decode path: {'convolutional interleaver / LDM' if watch.plan['mode'] == 'ldm' else 'hybrid interleaver'}")
            if not a.quiet:
                snr = f"{watch.snr[-1]:5.1f} dB" if watch.snr else "   -    "
                print(f"[{time.time() - t0:6.1f}s] frames {sync.n_frames:4d} cut {dec.n_frames:4d} decoded "
                      f"{sync.n_dropped:3d} dropped {sync.n_reacquire:2d} re-acq | FEC {dec.n_conv}/{dec.n_fec} conv, {dec.n_bch} BCH-clean | "
                      f"SNR {snr} | {alp.n_datagrams} datagrams", flush=True)
            if live:
                if a.seconds and time.time() - t0 >= a.seconds:
                    why = f"{a.seconds:.0f} s elapsed"
                    break
            else:
                state = (src.eof, sync.n_frames, dec.n_frames, alp.n_datagrams)
                still = still + 1 if state == last else 0
                last = state
                if src.eof:
                    break
        if stop.is_set():
            why = "interrupted"
        if live:
            # a radio: close the stream promptly; the frames still in flight are a fraction of a second
            tb.stop()
            tb.wait()
        else:
            # a file: drain stage by stage while messages still flow, so the tail is not lost
            sync.flush()
            t = time.time()
            while dec.n_in < sync.n_frames and time.time() - t < 600:
                time.sleep(0.1)
            dec.flush()
            t = time.time()
            while alp.n_bb < dec.n_frames and time.time() - t < 30:
                time.sleep(0.1)
            alp.flush()
            time.sleep(0.5)
            tb.stop()
            tb.wait()
        wall = time.time() - t0

    print(f"\nstopped: {why}")
    print(f"frames    {sync.n_frames} cut, {dec.n_frames} decoded, {sync.n_dropped} dropped (decoder behind); "
          f"{sync.n_breaks} sample-continuity breaks, {sync.n_reacquire} re-acquisitions")
    pct = 100.0 * dec.n_bch / dec.n_fec if dec.n_fec else 0.0
    print(f"FEC       {dec.n_conv}/{dec.n_fec} converged, {dec.n_bch} BCH-clean ({pct:.2f}%)")
    if watch.snr:
        s = sorted(watch.snr)
        print(f"SNR       median {s[len(s) // 2]:.1f} dB  (min {s[0]:.1f}, max {s[-1]:.1f}) off the known dummy cells")
    print(f"datagrams {alp.n_datagrams} in {wall:.0f} s wall; link-layer packets of other types: {alp.other_types}")
    for s in sinks:
        if hasattr(s, "flows"):
            for (dst, port), n in sorted(s.flows.items(), key=lambda kv: -kv[1])[:12]:
                print(f"  udp {dst}:{port}  {n}")
        if hasattr(s, "close"):
            print(f"  wrote {a.dump_dg}  sha256 {s.close()[:16]}...")
    return 0 if dec.n_bch else 1


if __name__ == "__main__":
    sys.exit(main())
