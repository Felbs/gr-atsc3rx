#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
import os
import queue
import tempfile
import time

import numpy as np
import pmt
from gnuradio import gr, gr_unittest

from qa_common import FRAME, FakeFrontEnd, _core, atsc3rx, fake_receiver, sink

RATE = 40000.0            # with SNIFF_SEC = 0.75 the sniff needs 30000 samples


def post(block, port, msg):
    block.to_basic_block()._post(pmt.intern(port), msg)


class qa_frame_sync(gr_unittest.TestCase):

    def setUp(self):
        with _core._win_cv:
            _core._windows.clear()

    def _graph(self, n, root, **kw):
        tb = gr.top_block()
        data = (np.arange(n) % 97 + 1).astype(np.complex64)
        # NOT a vector source: when a finite source ends, GNU Radio winds the flowgraph down and
        # message blocks stop receiving - with frames still in flight. Capture Source goes quiet.
        path = os.path.join(tempfile.mkdtemp(), "qa.cf32")
        data.tofile(path)
        src = atsc3rx.capture_source(path)
        sync = atsc3rx.frame_sync(RATE, root, block=5000, **kw)
        out = sink("frames", "plan", claim=True)
        tb.connect(src, sync)
        tb.keep = src                   # a Python block must outlive its flowgraph: connect() holds only the C++ half
        tb.msg_connect(sync, "frames", out, "frames")
        tb.msg_connect(sync, "plan", out, "plan")
        return tb, sync, out, data

    def test_001_every_sample_reaches_the_front_end_in_order_and_frames_go_by_reference(self):
        root, _ = fake_receiver()
        tb, sync, out, data = self._graph(12 * FRAME, root)
        tb.start()
        time.sleep(1.5)
        sync.flush()
        time.sleep(0.5)
        tb.stop()
        tb.wait()
        plan = pmt.to_python(out.got["plan"][0])
        self.assertEqual(plan["mode"], "m9")
        self.assertIn("fake plan", plan["describe"])
        fe = FakeFrontEnd.instances[-1]
        self.assertEqual(fe.pushed, 12 * FRAME)              # nothing lost between work() and the front end
        self.assertEqual(fe.frame_samples, FRAME)            # tracker retuned to the SIGNALLED frame length
        self.assertEqual(len(out.got["frames"]), 12)
        for m in out.got["frames"]:
            meta = pmt.to_python(pmt.car(m))
            self.assertEqual(pmt.length(pmt.cdr(m)), 0)      # by reference: the PDU carries a key
            self.assertEqual(meta["acq"], 0)
            self.assertTrue(meta["plan"])
        self.assertTrue(np.array_equal(np.concatenate(out.windows), data.astype(np.complex128)))

    def test_002_inline_puts_the_samples_in_the_pdu(self):
        root, _ = fake_receiver()
        tb, sync, out, data = self._graph(8 * FRAME, root, inline=True)
        tb.start()
        time.sleep(1.5)
        sync.flush()
        time.sleep(0.5)
        tb.stop()
        tb.wait()
        self.assertEqual(len(out.got["frames"]), 8)
        first = np.array(pmt.c64vector_elements(pmt.cdr(out.got["frames"][0])))
        self.assertTrue(np.array_equal(first, data[:FRAME].astype(np.complex128)))

    def test_003_a_run_of_weak_frames_re_acquires_and_one_good_frame_forgives(self):
        root, _ = fake_receiver()
        tb, sync, out, _ = self._graph(10 * FRAME, root)
        tb.start()
        time.sleep(1.5)
        fe = FakeFrontEnd.instances[-1]
        weak = pmt.to_pmt({"frame": 0, "acq": 0, "weak": True})
        good = pmt.to_pmt({"frame": 0, "acq": 0, "weak": False})
        for _ in range(7):
            post(sync, "feedback", weak)
        post(sync, "feedback", good)
        for _ in range(7):
            post(sync, "feedback", weak)
        time.sleep(0.5)
        self.assertEqual(fe.ft.full_requests, 14)            # every weak frame asks for a full timing rescan
        self.assertFalse(sync._want_reacquire)               # 7 + good + 7: never 8 in a row
        post(sync, "feedback", weak)
        time.sleep(0.5)
        self.assertTrue(sync._want_reacquire)
        sync._bad_run = 0
        post(sync, "feedback", pmt.to_pmt({"frame": 0, "acq": 99, "weak": True}))
        time.sleep(0.3)
        self.assertEqual(sync._bad_run, 0)                   # a verdict on another acquisition is ignored
        tb.stop()
        tb.wait()

    def test_004_re_acquisition_bumps_the_generation_the_decoder_watches(self):
        root, _ = fake_receiver()
        tb, sync, out, _ = self._graph(8 * FRAME, root)
        tb.start()
        time.sleep(1.5)
        sync._want_reacquire = True
        sync._q.put(np.ones(FRAME, np.complex64))
        time.sleep(0.5)
        tb.stop()
        tb.wait()
        self.assertEqual(FakeFrontEnd.instances[-1].reacquired, 1)
        self.assertEqual(sync.n_reacquire, 1)
        last = pmt.to_python(pmt.car(out.got["frames"][-1]))
        self.assertEqual(last["acq"], 1)

    def test_005_a_radio_counts_a_full_queue_as_a_continuity_break(self):
        root, _ = fake_receiver()
        sync = atsc3rx.frame_sync(RATE, root, block=100, drop_when_behind=True)
        sync.stop()                                          # nobody is draining the queue now
        sync._q = queue.Queue(maxsize=1)
        for _ in range(3):
            sync._acc, sync._acc_n = [np.ones(100, np.complex64)], 100
            sync._hand_over()
        self.assertEqual(sync.n_breaks, 2)
        self.assertTrue(sync._break)

    def test_006_settle_discards_the_first_moments_of_a_radio(self):
        root, _ = fake_receiver()
        tb, sync, out, _ = self._graph(12 * FRAME, root, settle_sec=0.1)     # 4000 samples
        tb.start()
        time.sleep(1.5)
        sync.flush()
        tb.stop()
        tb.wait()
        self.assertEqual(FakeFrontEnd.instances[-1].pushed, 12 * FRAME - 4000)

    def test_007_a_radio_that_shows_no_l1_keeps_listening(self):
        root, _ = fake_receiver(sniff_ok=False)
        tb, sync, out, _ = self._graph(12 * FRAME, root, drop_when_behind=True)
        tb.start()
        time.sleep(1.5)
        tb.stop()
        tb.wait()
        self.assertIsNone(sync.fe)
        self.assertGreaterEqual(len(out.got["plan"]), 1)
        self.assertIn("did not verify", pmt.to_python(out.got["plan"][0])["describe"])
        self.assertEqual(len(out.got["frames"]), 0)


if __name__ == '__main__':
    gr_unittest.run(qa_frame_sync)
