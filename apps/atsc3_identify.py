#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""What is this ATSC 3.0 carrier transmitting?

Finds the bootstrap, decodes L1-Basic and L1-Detail, and prints the frame plan: FFT size,
guard interval, pilot pattern, symbols per subframe, frame length, PLPs with their
modulation and code rate, time interleaver mode, LDM. Nothing is demodulated beyond the
signalling, so it answers in about a second of signal.

  atsc3_identify.py --capture FILE.cs16 [--rate 6.912e6]
  atsc3_identify.py --soapy "driver=..." --freq HZ [--antenna NAME] [--gain ELEMENT=VALUE ...]

Python phase: needs the reference receiver (--receiver or ATSC3_RECEIVER_DIR)."""
import argparse
import os
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


class plan_catcher(gr.basic_block):
    def __init__(self):
        gr.basic_block.__init__(self, name="plan_catcher", in_sig=None, out_sig=None)
        self.doc, self.got = None, threading.Event()
        self.message_port_register_in(pmt.intern("plan"))
        self.set_msg_handler(pmt.intern("plan"), self.on_plan)

    def on_plan(self, msg):
        self.doc = pmt.to_python(msg)
        self.got.set()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture")
    ap.add_argument("--soapy")
    ap.add_argument("--freq", type=float)
    ap.add_argument("--antenna")
    ap.add_argument("--gain", nargs="*", default=[], metavar="ELEMENT=VALUE")
    ap.add_argument("--rate", type=float, default=6.912e6)
    ap.add_argument("--receiver", default=os.environ.get("ATSC3_RECEIVER_DIR", ""))
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--json", action="store_true", help="print the machine-readable plan as well")
    a = ap.parse_args()
    if not (a.capture or (a.soapy and a.freq)):
        ap.error("give --capture FILE, or --soapy ARGS with --freq HZ")

    tb = gr.top_block()
    if a.capture:
        src = atsc3rx.capture_source(a.capture)
    else:
        from gnuradio import soapy
        src = soapy.source(a.soapy, "fc32", 1, "", "", [""], [""])
        src.set_sample_rate(0, a.rate)
        src.set_frequency(0, a.freq)
        src.set_gain_mode(0, False)
        if a.antenna:
            src.set_antenna(0, a.antenna)
        for item in a.gain:
            name, val = item.split("=")
            src.set_gain(0, name, float(val))
    sync = atsc3rx.frame_sync(a.rate, a.receiver)
    catch = plan_catcher()
    tb.connect(src, sync)
    tb.msg_connect(sync, "plan", catch, "plan")
    t0 = time.time()
    tb.start()
    ok = catch.got.wait(a.timeout)
    tb.stop()
    tb.wait()
    if not ok:
        print(f"no ATSC 3.0 bootstrap / L1 found in {a.timeout:.0f} s")
        return 1
    d = catch.doc
    print(d["describe"])
    print(f"decode path: {'convolutional interleaver / LDM pipeline' if d['mode'] == 'ldm' else 'hybrid interleaver'}"
          f"   ({time.time() - t0:.1f} s)")
    if a.json and d.get("spec_json"):
        print(d["spec_json"])
    return 0 if d.get("spec_json") else 2


if __name__ == "__main__":
    sys.exit(main())
