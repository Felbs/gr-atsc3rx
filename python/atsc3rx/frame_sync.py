#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Frame Sync: complex baseband in; one PDU per ATSC 3.0 frame out.

It finds the bootstrap, reads L1-Basic and L1-Detail, builds the frame PLAN from what
the broadcaster signals (never from the channel number), then tracks frame timing and
cuts one window of samples per frame. Everything about the geometry arrives over the
air, so it travels WITH the data: every frame PDU carries the plan in its metadata.

Python phase: this block drives the reference receiver's front end unchanged, exactly
as its live chain does (sniff -> plan -> front end, tracker retuned to the signalled
frame length), so its output can be held bit-exact against that receiver."""
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pmt
from gnuradio import gr

from . import _core

SNIFF_SEC = 0.75          # as the reference receiver: read this much air before asking L1


class frame_sync(gr.sync_block):
    """
    in : complex stream at `samp_rate` (any rate; resampled to 6.912 MS/s inside)
    out: message 'frames'  PDU  meta {index, t0, coh, mode, ps, plan}  data = c64vector window
         message 'plan'    {describe, spec_json, mode}   once per acquisition
    """

    def __init__(self, samp_rate=6.912e6, receiver_dir="", fe_threads=4, fast=True):
        gr.sync_block.__init__(self, name="atsc3rx_frame_sync", in_sig=[np.complex64], out_sig=None)
        self.rx = _core.load(receiver_dir or None)
        self.rate = float(samp_rate)
        self.fast = bool(fast)
        self.ex = ThreadPoolExecutor(max_workers=int(fe_threads))
        self.fe = None
        self.plan = None
        self.mode = "m9"
        self._held = []
        self._held_n = 0
        self.n_frames = 0
        self.message_port_register_out(pmt.intern("frames"))
        self.message_port_register_out(pmt.intern("plan"))

    # ---- acquisition: exactly the reference receiver's order of operations ----------
    def _sniff(self):
        rx = self.rx
        plan, info = rx.M44.sniff(np.concatenate(self._held), self.rate, log=rx.ST.log)
        self.plan = plan
        if plan is not None and plan.uses_cti:
            self.mode = "ldm"                 # convolutional time interleaver: the LDM pipeline
            self.fe = rx.ST.FrontEnd(self.rate, ex=self.ex, fast=self.fast, plan=plan)
        else:
            self.mode = "m9"                  # hybrid time interleaver (or unidentified)
            self.fe = rx.ST.FrontEnd(self.rate, ex=self.ex, fast=self.fast, plan=None)
            if plan is not None:
                # the timing tracker must advance by the frame length L1 SIGNALS; a wrong
                # constant cost the reference receiver a whole campaign. Advance only - the
                # window stays as the decoder expects it.
                self.fe.frame_samples = plan.frame_samples
                self.fe.ft.plan = plan
                self.fe.ft.frame_samples = plan.frame_samples
                self.fe.ft.reset()
        doc = {"mode": self.mode,
               "describe": plan.describe() if plan is not None else
               "L1 did not verify: " + str(info.get("why", "unidentified")),
               "spec_json": json.dumps(plan.to_spec()) if plan is not None else ""}
        self.message_port_pub(pmt.intern("plan"), pmt.to_pmt(doc))
        for x in self._held:                  # hand the sniffed air back, in order
            self._push(x)
        self._held = None

    def _push(self, x):
        self.fe.push(x)
        for idx, w, t0, coh in self.fe.frames():
            meta = {"index": int(idx), "t0": int(t0), "coh": float(coh), "mode": self.mode,
                    "ps": int(self.fe.ps) if getattr(self.fe, "ps", None) is not None else -1,
                    "plan": json.dumps(self.plan.to_spec()) if self.plan is not None else ""}
            self.message_port_pub(pmt.intern("frames"),
                                  pmt.cons(pmt.to_pmt(meta), pmt.to_pmt(np.asarray(w, np.complex128))))
            self.n_frames += 1

    def work(self, input_items, output_items):
        x = np.array(input_items[0], dtype=np.complex64)      # own the memory: the buffer is reused
        if self._held is not None:
            self._held.append(x)
            self._held_n += len(x)
            if self._held_n >= int(SNIFF_SEC * self.rate):
                self._sniff()
        else:
            self._push(x)
        return len(x)

    def stop(self):
        if self._held is not None and self._held_n > 0:       # a very short input: still try
            self._sniff()
        self.ex.shutdown(wait=False)
        return True
