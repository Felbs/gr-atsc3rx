#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Frame Decoder: one frame of samples in; baseband payload out, with proof.

Per frame: OFDM demodulation, channel estimation, common-phase-error correction,
de-interleaving, soft demapping, LDPC, BCH, descrambling - then the baseband packets
are joined into a byte stream with the packet boundaries the link layer needs.

It also publishes the two numbers a tuner wants (see gr-rxtune): a DIAL that is
continuous below the decode cliff - SNR read off the frame's known dummy cells - and
LIVENESS that cannot be faked - the running count of FEC blocks that came out BCH-clean.

Python phase: the reference receiver's decoders, unchanged. Hybrid-interleaver
multiplexes use its fast frame decoder; convolutional-interleaver (incl. LDM) ones use
its LDM pipeline. In C++ this block splits into the stages listed in docs/DESIGN.md."""
import json
import time
import threading

import numpy as np
import pmt
from gnuradio import gr

from . import _core


class frame_decoder(gr.basic_block):
    """
    in : message 'frames' (from Frame Sync)
         message 'plan'   (from Frame Sync; optional) - prewarm as soon as the plan is known
    out: message 'bb'       PDU meta {frame, bounds, converged, bch_ok, n_fec} data = u8vector
         message 'quality'  {frame, snr_db, converged, bch_ok, n_fec, dummy}
         message 'dial'     (SNR . dB)            for rxtune
         message 'liveness' cumulative BCH-clean FEC blocks
         message 'feedback' {frame, acq, weak}    back to Frame Sync: the decoder is the only
                            stage that KNOWS whether timing is right. Weak = under 81% of the
                            frame's FEC blocks converged (hybrid), or no interleaver phase (CTI).
    """

    def __init__(self, receiver_dir="", threads=8, iters=50, cpu_fast=True, procs=0, proc_threads=4):
        gr.basic_block.__init__(self, name="atsc3rx_frame_decoder", in_sig=None, out_sig=None)
        self.rx = _core.load(receiver_dir or None)
        self.threads, self.iters, self.cpu_fast = int(threads), int(iters), bool(cpu_fast)
        self.procs, self.proc_threads = int(procs), int(proc_threads)   # LDM path: demodulate in worker processes
        self.dec = None
        self.mode = None
        self.bb_abs = 0
        self.n_frames = self.n_conv = self.n_bch = self.n_fec = 0
        self._lock = threading.Lock()
        self.t_busy = 0.0
        self.n_in = 0                           # frames fully handled (published or not)
        self.message_port_register_in(pmt.intern("frames"))
        self.set_msg_handler(pmt.intern("frames"), self.on_frame)
        self.message_port_register_in(pmt.intern("plan"))
        self.set_msg_handler(pmt.intern("plan"), self.on_plan)
        self.acq = None
        for p in ("bb", "quality", "dial", "liveness", "feedback"):
            self.message_port_register_out(pmt.intern(p))

    def _build(self, mode, plan_json):
        rx = self.rx
        plan = rx.M44.LdmPlan.from_spec(json.loads(plan_json)) if plan_json else None
        if mode == "ldm":
            self.dec = rx.M44.LdmPipeline(plan=plan, iters=self.iters, threads=self.threads, accel="cpu",
                                          log=rx.ST.log, procs=self.procs, proc_threads=self.proc_threads)
            self.dec.adopt(plan)
        else:
            self.dec = rx.m9_fast.FrameDecoder(threads=self.threads, backend="cpu", iters=self.iters,
                                               cpu_fast=self.cpu_fast, plan=plan)
        self.dec.prewarm()
        self.mode = mode

    def on_plan(self, msg):
        """Optional: build the decoder's tables when the plan is known, not under the first frames.
        On a radio those seconds of table building otherwise cost the first ~10 frames."""
        d = pmt.to_python(msg)
        if isinstance(d, dict) and d.get("mode") and d.get("spec_json"):
            with self._lock:
                if self.dec is None:
                    self._build(d["mode"], d["spec_json"])

    def on_frame(self, msg):
        meta = pmt.to_python(pmt.car(msg))
        w = _core.claim_window(meta["win"]) if "win" in meta else None
        if w is None:
            if pmt.length(pmt.cdr(msg)) == 0:
                self.n_in += 1
                return                                  # by-reference frame from another process, or already claimed
            w = np.asarray(pmt.c64vector_elements(pmt.cdr(msg)), dtype=np.complex128)
        tw = time.perf_counter()
        with self._lock:
            if self.dec is None:
                self._build(meta["mode"], meta.get("plan", ""))
            acq = meta.get("acq", 0)
            if self.acq is not None and acq != self.acq and self.mode == "ldm":
                self.dec.reset()                        # the front end re-acquired: interleaver state is void
            self.acq = acq
            if self.mode == "ldm":
                self._ldm(meta, w)
                ph = getattr(self.dec, "ph", None)
                weak = ph is not None and ph.state == "cold"
            else:
                weak = self._m9(meta, w)
        self.n_in += 1
        self.message_port_pub(pmt.intern("feedback"), pmt.to_pmt(
            {"frame": int(meta["index"]), "acq": int(acq), "weak": bool(weak)}))
        self.t_busy += time.perf_counter() - tw

    def _m9(self, meta, w):
        rx = self.rx
        r16, pkts, diag = self.dec.decode_frame(w, int(meta["t0"]))
        stream, bounds = bytearray(), []
        for p in pkts:
            if p is None:
                continue
            ptr, pay = rx.P6.bb_split(p)
            if ptr is not None:
                bounds.append(self.bb_abs + len(stream) + ptr)     # absolute: the link layer resumes across frames
            stream += pay
        self.bb_abs += len(stream)
        conv, n_fec = int(diag.get("converged", 0)), int(diag.get("n_fec", 0))
        self._publish(meta, bytes(stream), bounds, conv, diag.get("bch_ok", 0),
                      n_fec, diag.get("snr_db"), diag.get("dummy"))
        return conv < min(60, int(round(0.81 * n_fec))) if n_fec else True

    def _ldm(self, meta, w):
        ps = meta.get("ps", -1)
        before = (self.dec.n_conv, self.dec.n_bch, self.dec.n_blocks)
        for stream, bounds in self.dec.push_frame(int(meta["index"]), w, int(meta["t0"]),
                                                  ps=None if ps < 0 else ps):
            conv, bch, blocks = self.dec.n_conv, self.dec.n_bch, self.dec.n_blocks
            self._publish(meta, bytes(stream), list(bounds), conv - before[0], bch - before[1],
                          blocks - before[2], None, None)
            before = (conv, bch, blocks)

    def _publish(self, meta, stream, bounds, conv, bch, n_fec, snr_db, dummy):
        self.n_frames += 1
        self.n_conv += int(conv)
        self.n_bch += int(bch)
        self.n_fec += int(n_fec)
        m = {"frame": int(meta["index"]), "bounds": [int(b) for b in bounds], "converged": int(conv),
             "bch_ok": int(bch), "n_fec": int(n_fec)}
        self.message_port_pub(pmt.intern("bb"), pmt.cons(
            pmt.to_pmt(m), pmt.init_u8vector(len(stream), list(stream))))
        q = dict(m)
        q.pop("bounds")
        if isinstance(snr_db, (int, float)):
            q["snr_db"] = float(snr_db)
            self.message_port_pub(pmt.intern("dial"), pmt.cons(pmt.intern("SNR"), pmt.from_double(float(snr_db))))
        if isinstance(dummy, (int, float)):
            q["dummy"] = float(dummy)
        elif isinstance(dummy, dict):               # the reference decoder reports a small dict here
            q.update({f"dummy_{k}": float(v) for k, v in dummy.items() if isinstance(v, (int, float))})
        self.message_port_pub(pmt.intern("quality"), pmt.to_pmt(q))
        self.message_port_pub(pmt.intern("liveness"), pmt.from_long(self.n_bch))

    def flush(self):
        """End of input: the LDM pipeline holds interleaver state."""
        with self._lock:
            if self.mode == "ldm" and self.dec is not None:
                before = (self.dec.n_conv, self.dec.n_bch, self.dec.n_blocks)
                for stream, bounds in self.dec.flush():
                    conv, bch, blocks = self.dec.n_conv, self.dec.n_bch, self.dec.n_blocks
                    self._publish({"index": -1}, bytes(stream), list(bounds), conv - before[0], bch - before[1],
                                  blocks - before[2], None, None)
                    before = (conv, bch, blocks)

    def stop(self):
        try:
            if self.mode == "m9" and self.dec is not None:
                self.dec.close()
        except Exception:
            pass
        return True
