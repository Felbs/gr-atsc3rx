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
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pmt
from gnuradio import gr

from . import _core

SNIFF_SEC = 0.75          # as the reference receiver: read this much air before asking L1


class frame_sync(gr.sync_block):
    """
    in : complex stream at `samp_rate` (any rate; resampled to 6.912 MS/s inside)
    out: message 'frames'  PDU  meta {index, t0, coh, mode, ps, plan, win}  data = c64vector window
                           (inline=False: the window stays in this process and `win` is its key;
                            the vector is empty. See _core.park_window.)
         message 'plan'    {describe, spec_json, mode}   once per acquisition
    """

    def __init__(self, samp_rate=6.912e6, receiver_dir="", fe_threads=8, fast=True, inline=False, drop_when_behind=False, block=854000):
        gr.sync_block.__init__(self, name="atsc3rx_frame_sync", in_sig=[np.complex64], out_sig=None)
        self.rx = _core.load(receiver_dir or None)
        self.rate = float(samp_rate)
        self.fast = bool(fast)
        self.inline = bool(inline)
        self.drop = bool(drop_when_behind)      # True for a radio, False for a file
        self.n_dropped = 0
        self.t_busy = 0.0
        self.t_wait = 0.0                       # of which: waiting for the decoder to take a frame
        self.block = int(block)                 # feed the front end in blocks this size, not GNU Radio buffers
        self._acc, self._acc_n = [], 0
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
        self._plan_json = json.dumps(plan.to_spec()) if plan is not None else ""
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
               "spec_json": self._plan_json}
        self.message_port_pub(pmt.intern("plan"), pmt.to_pmt(doc))
        for x in self._held:                  # hand the sniffed air back, in order
            self._push(x)
        self._held = None

    def _push(self, x):
        self.fe.push(x)
        for idx, w, t0, coh in self.fe.frames():
            meta = {"index": int(idx), "t0": int(t0), "coh": float(coh), "mode": self.mode,
                    "ps": int(self.fe.ps) if getattr(self.fe, "ps", None) is not None else -1,
                    "plan": self._plan_json}
            w = np.asarray(w, np.complex128)
            if self.inline:
                body = pmt.init_c64vector(len(w), w)
            else:
                tp = time.perf_counter()
                key = _core.park_window(w, drop=self.drop)
                self.t_wait += time.perf_counter() - tp
                if key is None:
                    self.n_dropped += 1
                    continue
                meta["win"] = key
                body = pmt.make_c64vector(0, 0j)
            self.message_port_pub(pmt.intern("frames"), pmt.cons(pmt.to_pmt(meta), body))
            self.n_frames += 1

    def work(self, input_items, output_items):
        tw = time.perf_counter()
        x = np.array(input_items[0], dtype=np.complex64)      # own the memory: the buffer is reused
        if self._held is not None:
            self._held.append(x)
            self._held_n += len(x)
            if self._held_n >= int(SNIFF_SEC * self.rate):
                self._sniff()
        else:
            self._acc.append(x)
            self._acc_n += len(x)
            if self._acc_n >= self.block:
                self._drain()
        self.t_busy += time.perf_counter() - tw
        return len(x)

    def _drain(self):
        if self._acc_n:
            x = np.concatenate(self._acc) if len(self._acc) > 1 else self._acc[0]
            self._acc, self._acc_n = [], 0
            self._push(x)

    def stop(self):
        if self._held is not None and self._held_n > 0:       # a very short input: still try
            self._sniff()
        elif self._held is None:
            self._drain()
        self.ex.shutdown(wait=False)
        return True
