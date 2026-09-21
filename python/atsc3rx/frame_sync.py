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

Three things keep it honest on a real radio:
  * work() only queues samples. Acquisition takes a second or two of CPU; done inside
    work() it stalls the flowgraph, the radio overflows, and the timing tracker then
    dead-reckons across a hole it cannot see. (Measured: 3 clean frames, then nothing.)
  * A full queue on a radio is a CONTINUITY BREAK: the block drops the input, counts it,
    and re-acquires rather than pretend the tracking survived. A file waits instead.
  * The decoder's verdict comes back on 'feedback'. A weak frame asks for a full timing
    rescan; a run of them re-acquires. Coherence alone measured blind while FEC fell to 0.

Python phase: this block drives the reference receiver's front end unchanged, exactly
as its live chain does (sniff -> plan -> front end, tracker retuned to the signalled
frame length), so its output can be held bit-exact against that receiver."""
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pmt
from gnuradio import gr

from . import _core

SNIFF_SEC = 0.75          # as the reference receiver: read this much air before asking L1
BAD_RUN = 8               # consecutive weak frames before re-acquiring (reference default)
QUEUE_BLOCKS = 16         # ~2 s of samples between work() and the front end


class frame_sync(gr.sync_block):
    """
    in : complex stream at `samp_rate` (any rate; resampled to 6.912 MS/s inside)
         message 'feedback' {frame, acq, weak} from Frame Decoder (optional; connect it on air)
    out: message 'frames'  PDU  meta {index, t0, coh, mode, ps, plan, acq, win}  data = c64vector window
                           (inline=False: the window stays in this process and `win` is its key;
                            the vector is empty. See _core.park_window.)
         message 'plan'    {describe, spec_json, mode}   once per acquisition
    """

    def __init__(self, samp_rate=6.912e6, receiver_dir="", fe_threads=8, fast=True, inline=False,
                 drop_when_behind=False, block=854000, settle_sec=0.0):
        gr.sync_block.__init__(self, name="atsc3rx_frame_sync", in_sig=[np.complex64], out_sig=None)
        self.rx = _core.load(receiver_dir or None)
        self.rate = float(samp_rate)
        self.fast = bool(fast)
        self.inline = bool(inline)
        self.drop = bool(drop_when_behind)      # True for a radio, False for a file
        self._skip = int(float(settle_sec) * self.rate)   # a radio's first moments: tuner and gain still settling
        self.block = int(block)                 # feed the front end in blocks this size, not GNU Radio buffers
        self.ex = ThreadPoolExecutor(max_workers=int(fe_threads))
        self.fe = None
        self.plan = None
        self._plan_json = ""
        self.mode = "m9"
        self.acq = 0                            # acquisition generation; the decoder resets on a change
        self.n_frames = self.n_dropped = self.n_breaks = self.n_reacquire = 0
        self.t_busy = self.t_wait = 0.0
        self._acc, self._acc_n = [], 0
        self._held, self._held_n = [], 0
        self._q = queue.Queue(maxsize=QUEUE_BLOCKS)
        self._break = False
        self._bad_run = 0
        self._want_reacquire = False
        self._quit = False
        self._worker = threading.Thread(target=self._run, name="atsc3rx-frontend", daemon=True)
        self._worker.start()
        self.message_port_register_out(pmt.intern("frames"))
        self.message_port_register_out(pmt.intern("plan"))
        self.message_port_register_in(pmt.intern("feedback"))
        self.set_msg_handler(pmt.intern("feedback"), self.on_feedback)

    # ---- GNU Radio side: cheap, never blocks a radio ---------------------------------
    def work(self, input_items, output_items):
        n_in = len(input_items[0])
        if self._skip > 0:
            k = min(self._skip, n_in)
            self._skip -= k
            if k == n_in:
                return n_in
            x = np.array(input_items[0][k:], dtype=np.complex64)
        else:
            x = np.array(input_items[0], dtype=np.complex64)  # own the memory: the buffer is reused
        self._acc.append(x)
        self._acc_n += len(x)
        if self._acc_n >= self.block:
            self._hand_over()
        return n_in

    def _hand_over(self):
        if not self._acc_n:
            return
        blk = np.concatenate(self._acc) if len(self._acc) > 1 else self._acc[0]
        self._acc, self._acc_n = [], 0
        if self.drop:
            try:
                self._q.put_nowait(blk)
            except queue.Full:                  # the air does not wait: this is a hole in the samples
                self.n_breaks += 1
                self._break = True
        else:
            while not self._quit:
                try:
                    self._q.put(blk, timeout=0.25)
                    break
                except queue.Full:
                    pass

    def on_feedback(self, msg):
        d = pmt.to_python(msg)
        if not isinstance(d, dict) or d.get("acq", self.acq) != self.acq:
            return                              # a verdict on frames from before the last re-acquisition
        if d.get("weak"):
            self._bad_run += 1
            if self.mode == "m9" and self.fe is not None:
                try:
                    self.fe.ft.request_full()   # a weak frame is the timing tracker's real guard
                except AttributeError:
                    pass
            if self._bad_run >= BAD_RUN:
                self._bad_run = 0
                self._want_reacquire = True
        else:
            self._bad_run = 0

    # ---- front-end thread ------------------------------------------------------------
    def _run(self):
        while True:
            blk = self._q.get()
            if blk is None:
                self._q.task_done()
                return
            t = time.perf_counter()
            try:
                self._feed(blk)
            except Exception as e:                                  # noqa: BLE001
                self.rx.lines.append(f"front end: {type(e).__name__}: {e}")
                self._reacquire()
            self.t_busy += time.perf_counter() - t
            self._q.task_done()

    def _feed(self, x):
        if self.fe is None:
            if self._break:                     # a hole inside the sniff window: start it again
                self._break = False
                self._held, self._held_n = [], 0
            self._held.append(x)
            self._held_n += len(x)
            if self._held_n >= int(SNIFF_SEC * self.rate) or len(x) == 0:
                self._sniff()
            return
        if self._break or self._want_reacquire:
            self._break = self._want_reacquire = False
            self._reacquire()
        self._push(x)

    def _reacquire(self):
        if self.fe is not None:
            self.fe.reacquire()
        self.acq += 1
        self.n_reacquire += 1
        self._bad_run = 0

    # acquisition: exactly the reference receiver's order of operations
    def _sniff(self):
        rx = self.rx
        held, self._held, self._held_n = self._held, [], 0
        plan, info = rx.M44.sniff(np.concatenate(held), self.rate, log=rx.ST.log)
        if plan is None and self.drop:
            # a radio that has not shown us L1 yet: keep listening rather than commit to a guess
            self.message_port_pub(pmt.intern("plan"), pmt.to_pmt(
                {"mode": "", "describe": "L1 did not verify: " + str(info.get("why", "unidentified")),
                 "spec_json": ""}))
            return
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
        for x in held:                        # hand the sniffed air back, in order
            self._push(x)

    def _push(self, x):
        if len(x) == 0:
            return
        self.fe.push(x)
        for idx, w, t0, coh in self.fe.frames():
            meta = {"index": int(idx), "t0": int(t0), "coh": float(coh), "mode": self.mode, "acq": self.acq,
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

    # ---- end of input ----------------------------------------------------------------
    def flush(self, timeout=600.0):
        """A file has ended: push what is buffered through the front end and wait for it.

        Safe to call after the flowgraph has stopped this block: with the front-end thread
        gone the queue is drained here instead, so the tail of a capture is never lost and
        this never waits on a thread that has exited."""
        t = time.time()
        if self._worker.is_alive():
            self._hand_over()
            if self.fe is None:
                self._q.put(np.zeros(0, np.complex64))            # a very short input: sniff what there is
            while self._q.unfinished_tasks and self._worker.is_alive() and time.time() - t < timeout:
                time.sleep(0.05)
        if not self._worker.is_alive():
            self._drain_here()

    def _drain_here(self):
        blocks = []
        while True:
            try:
                b = self._q.get_nowait()
            except queue.Empty:
                break
            if b is not None:
                blocks.append(b)
        if self._acc_n:
            blocks.append(np.concatenate(self._acc))
            self._acc, self._acc_n = [], 0
        if self.fe is None:
            blocks.append(np.zeros(0, np.complex64))
        for b in blocks:
            try:
                self._feed(b)
            except Exception as e:                                  # noqa: BLE001
                self.rx.lines.append(f"front end: {type(e).__name__}: {e}")

    def stop(self):
        self._quit = True
        try:
            self._q.put(None, timeout=2.0)
        except queue.Full:
            pass
        self._worker.join(timeout=5.0)
        self.ex.shutdown(wait=False)
        return True
