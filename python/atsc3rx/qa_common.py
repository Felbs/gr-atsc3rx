# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared by the QA files.

  * imports gnuradio.atsc3rx from an install OR the source tree;
  * `sink`: collects messages from named ports;
  * `fake_receiver()`: a stand-in for the reference receiver, so that everything these blocks
    ADD (threading, back-pressure, continuity breaks, the feedback supervisor, flushing, PDU
    formats) is tested in CI with no receiver, no capture and no radio. The decode itself is
    tested against the real receiver by util/gate_bitexact.py."""
import os
import sys
import tempfile
import types

import numpy as np
import pmt
from gnuradio import gr

EXAMPLES = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "examples"))
sys.path.insert(0, EXAMPLES)
try:
    from gnuradio import atsc3rx  # noqa: F401
except ImportError:
    import _devpath  # noqa: F401
    from gnuradio import atsc3rx  # noqa: F401
from gnuradio.atsc3rx import _core  # noqa: E402,F401


class sink(gr.basic_block):
    """Collects messages from any number of named ports."""

    def __init__(self, *ports, claim=False):
        gr.basic_block.__init__(self, name="qa_sink", in_sig=None, out_sig=None)
        self.got = {p: [] for p in ports}
        self.windows = []               # claim=True: take by-reference frame windows as a decoder would
        for p in ports:
            self.message_port_register_in(pmt.intern(p))

            # the Python gateway finds a handler again BY NAME, so a lambda will not do
            def handler(msg, p=p):
                self.got[p].append(msg)
                if claim and p == "frames":
                    meta = pmt.to_python(pmt.car(msg))
                    if "win" in meta:
                        self.windows.append(_core.claim_window(meta["win"]))
            handler.__name__ = "_h_" + p
            setattr(self, handler.__name__, handler)
            self.set_msg_handler(pmt.intern(p), handler)


FRAME = 4000              # the fake multiplex: one frame every 4000 samples


class FakePlan:
    frame_samples = FRAME

    def __init__(self, cti=False):
        self.uses_cti = cti

    def describe(self):
        return "fake plan: 1 PLP"

    def to_spec(self):
        return {"fake": True, "cti": self.uses_cti}

    @classmethod
    def from_spec(cls, d):
        return cls(d.get("cti", False))


class FakeTracker:
    def __init__(self):
        self.full_requests = self.resets = 0
        self.plan = None
        self.frame_samples = 0

    def request_full(self):
        self.full_requests += 1

    def reset(self):
        self.resets += 1


class FakeFrontEnd:
    """Cuts a 'frame' every FRAME samples; the window is the samples themselves."""
    instances = []

    def __init__(self, rate, ex=None, fast=True, plan=None, plan_cb=None):
        self.ft = FakeTracker()
        self.frame_samples = 0
        self.ps = 0
        self.buf = np.zeros(0, np.complex64)
        self.idx = 0
        self.reacquired = 0
        self.pushed = 0
        FakeFrontEnd.instances.append(self)

    def push(self, x):
        self.pushed += len(x)
        self.buf = np.concatenate([self.buf, x])

    def frames(self):
        out = []
        while len(self.buf) >= FRAME:
            w, self.buf = self.buf[:FRAME], self.buf[FRAME:]
            out.append((self.idx, w.astype(np.complex128), 0, 1.0))
            self.idx += 1
        return out

    def reacquire(self):
        self.reacquired += 1
        self.buf = np.zeros(0, np.complex64)


class FakeFrameDecoder:
    """'Decodes' a frame to 7 bytes; a frame whose first sample is negative fails FEC."""

    def __init__(self, **kw):
        self.kw = kw

    def prewarm(self):
        pass

    def close(self):
        pass

    def decode_frame(self, w, t0):
        # a frame whose first sample is 1000+ is one that ONLY the smoothing-off decoder can read
        if w[0].real >= 1000:
            ok = self.kw.get("margin") == {"ce_w": 0}
            return None, [bytes([0]) + bytes([9] * 7) if ok else None], {
                "converged": 10 if ok else 0, "bch_ok": 10 if ok else 0, "n_fec": 10, "snr_db": 17.0, "dummy": {"n": 5}}
        good = w[0].real >= 0
        pkt = bytes([0]) + bytes([int(abs(w[0].real)) % 256] * 7) if good else None
        return None, [pkt], {"converged": 10 if good else 0, "bch_ok": 10 if good else 0, "n_fec": 10,
                             "snr_db": 20.0 if good else -30.0, "dummy": {"n": 5}}


class FakeWalker:
    """Link layer: every 4 bytes of baseband is one packet; type = first byte. Holds a partial tail."""

    def __init__(self):
        self.buf = b""

    def feed(self, stream, bounds):
        self.buf += stream
        out = []
        while len(self.buf) >= 4:
            out.append((self.buf[0], self.buf[:4]))
            self.buf = self.buf[4:]
        return out

    def pump(self, final=False):
        out = [(self.buf[0], self.buf)] if final and self.buf else []
        self.buf = b""
        return out


class FakeIpReasm:
    def feed(self, payload):
        return [(bytes([10, 0, 0, 1]), bytes([239, 1, 2, 3]), 1000, 2000 + payload[1], payload)]


def fake_receiver(cti=False, sniff_ok=True):
    """Install a fake reference receiver; returns (receiver_dir, namespace)."""
    root = tempfile.mkdtemp(prefix="atsc3rx_qa_")
    os.makedirs(os.path.join(root, "lab"))
    ns = types.SimpleNamespace()
    ns.lines = []
    ns.ST = types.SimpleNamespace(FrontEnd=FakeFrontEnd, StreamAlpWalker=FakeWalker, log=lambda *a, **k: None)
    ns.R7 = types.SimpleNamespace(IpReasm=FakeIpReasm)

    def sniff(raw, rate, log=None):
        return (FakePlan(cti), {}) if sniff_ok else (None, {"why": "nothing there"})
    ns.M44 = types.SimpleNamespace(sniff=sniff, LdmPlan=FakePlan)
    ns.m9_fast = types.SimpleNamespace(FrameDecoder=FakeFrameDecoder)
    ns.P6 = types.SimpleNamespace(bb_split=lambda p: (p[0], p[1:]))
    FakeFrontEnd.instances.clear()
    _core._cache[os.path.join(os.path.abspath(root), "lab")] = ns
    return root, ns
