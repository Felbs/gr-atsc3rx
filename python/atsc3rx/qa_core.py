#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
import os
import threading
import time

import numpy as np
from gnuradio import gr_unittest

from qa_common import _core


class qa_core(gr_unittest.TestCase):

    def setUp(self):
        with _core._win_cv:
            _core._windows.clear()

    def test_001_a_window_is_claimed_once(self):
        w = np.arange(8, dtype=np.complex128)
        key = _core.park_window(w)
        self.assertIs(_core.claim_window(key), w)            # by reference: the same object, no copy
        self.assertIsNone(_core.claim_window(key))

    def test_002_a_radio_drops_whole_frames_when_the_decoder_is_behind(self):
        keys = [_core.park_window(np.zeros(1), drop=True) for _ in range(_core.WINDOW_CAP + 3)]
        self.assertEqual(sum(k is None for k in keys), 3)
        self.assertEqual(len(_core._windows), _core.WINDOW_CAP)   # never grows

    def test_003_a_file_waits_instead(self):
        keys = [_core.park_window(np.zeros(1)) for _ in range(_core.WINDOW_CAP)]
        got = []
        t = threading.Thread(target=lambda: got.append(_core.park_window(np.zeros(1), timeout=10)))
        t.start()
        time.sleep(0.3)
        self.assertEqual(got, [])                            # blocked, not dropped
        _core.claim_window(keys[0])
        t.join(5)
        self.assertEqual(len(got), 1)
        self.assertIsNotNone(got[0])

    def test_004_no_receiver_is_a_clear_error(self):
        old = os.environ.pop("ATSC3_RECEIVER_DIR", None)
        try:
            with self.assertRaises(_core.ReceiverNotFound):
                _core.load(None)
            with self.assertRaises(_core.ReceiverNotFound):
                _core.load("/nowhere/at/all")
        finally:
            if old is not None:
                os.environ["ATSC3_RECEIVER_DIR"] = old


if __name__ == '__main__':
    gr_unittest.run(qa_core)
