#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
import time

import numpy as np
import pmt
from gnuradio import gr, gr_unittest

from qa_common import FRAME, _core, atsc3rx, fake_receiver, sink


def post(block, port, msg):
    block.to_basic_block()._post(pmt.intern(port), msg)


class qa_frame_decoder(gr_unittest.TestCase):

    def _graph(self, root, *ports):
        dec = atsc3rx.frame_decoder(root)
        out = sink(*ports)
        tb = gr.top_block()
        for p in ports:
            tb.msg_connect(dec, p, out, p)
        return tb, dec, out

    def test_001_payload_quality_dial_liveness_and_feedback(self):
        root, _ = fake_receiver()
        tb, dec, out = self._graph(root, "bb", "quality", "dial", "liveness", "feedback")
        tb.start()
        for i, first in enumerate((5.0, -1.0, 7.0)):          # good, FEC failure, good
            w = np.full(FRAME, first, np.complex128)
            meta = {"index": i, "t0": 0, "coh": 1.0, "mode": "m9", "ps": 0, "plan": '{"fake": true}', "acq": 0,
                    "win": _core.park_window(w)}
            post(dec, "frames", pmt.cons(pmt.to_pmt(meta), pmt.make_c64vector(0, 0j)))
        time.sleep(1.0)
        tb.stop()
        tb.wait()
        self.assertEqual(dec.n_in, 3)
        self.assertEqual((dec.n_fec, dec.n_bch), (30, 20))
        fb = [pmt.to_python(m) for m in out.got["feedback"]]
        self.assertEqual([d["weak"] for d in fb], [False, True, False])
        self.assertEqual([pmt.to_python(m) for m in out.got["liveness"]], [10, 10, 20])   # cumulative: never goes down
        dial = out.got["dial"][0]
        self.assertEqual(pmt.symbol_to_string(pmt.car(dial)), "SNR")
        self.assertAlmostEqual(pmt.to_double(pmt.cdr(dial)), 20.0)
        self.assertEqual(pmt.to_python(out.got["quality"][0])["dummy_n"], 5.0)
        # baseband bytes: 7 per good frame; bounds are ABSOLUTE offsets into the stream
        bb = [(pmt.to_python(pmt.car(m)), bytes(pmt.u8vector_elements(pmt.cdr(m)))) for m in out.got["bb"]]
        self.assertEqual([len(b) for _, b in bb], [7, 0, 7])
        self.assertEqual([m["bounds"] for m, _ in bb], [[0], [], [7]])

    def test_002_inline_frames_work_without_the_window_registry(self):
        root, _ = fake_receiver()
        tb, dec, out = self._graph(root, "bb")
        tb.start()
        w = np.full(FRAME, 3.0, np.complex128)
        meta = {"index": 0, "t0": 0, "mode": "m9", "plan": "", "acq": 0}
        post(dec, "frames", pmt.cons(pmt.to_pmt(meta), pmt.init_c64vector(len(w), w)))
        time.sleep(0.8)
        tb.stop()
        tb.wait()
        self.assertEqual(dec.n_bch, 10)

    def test_004_a_frame_that_fails_at_a_healthy_snr_is_retried_without_smoothing(self):
        root, _ = fake_receiver()
        tb, dec, out = self._graph(root, "bb", "feedback")
        tb.start()
        for i, first in enumerate((1000.0, -1.0)):            # healthy-SNR failure (rescuable); a real fade (SNR -30)
            w = np.full(FRAME, first, np.complex128)
            meta = {"index": i, "t0": 0, "mode": "m9", "plan": "", "acq": 0, "win": _core.park_window(w)}
            post(dec, "frames", pmt.cons(pmt.to_pmt(meta), pmt.make_c64vector(0, 0j)))
        time.sleep(1.0)
        tb.stop()
        tb.wait()
        self.assertEqual((dec.n_retried, dec.n_rescued), (1, 1))      # the fade was NOT retried: low SNR is the air
        self.assertEqual(dec.n_bch, 10)
        self.assertEqual([pmt.to_python(m)["weak"] for m in out.got["feedback"]], [False, True])

    def test_003_a_by_reference_frame_nobody_parked_is_counted_not_crashed_on(self):
        root, _ = fake_receiver()
        tb, dec, out = self._graph(root, "bb")
        tb.start()
        meta = {"index": 0, "t0": 0, "mode": "m9", "plan": "", "acq": 0, "win": 987654321}
        post(dec, "frames", pmt.cons(pmt.to_pmt(meta), pmt.make_c64vector(0, 0j)))
        time.sleep(0.5)
        tb.stop()
        tb.wait()
        self.assertEqual(dec.n_in, 1)
        self.assertEqual(len(out.got["bb"]), 0)


if __name__ == '__main__':
    gr_unittest.run(qa_frame_decoder)
