#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Capture Source: a raw IQ file (.cs16 interleaved int16, or .cf32) as a complex stream.

Unlike File Source it does not END the flowgraph at end of file: it goes quiet and sets
`eof`. A receiver made of message blocks still has frames in flight at that moment, and a
flowgraph that shuts down under them loses the tail of the capture."""
import os
import time

import numpy as np
from gnuradio import gr


class capture_source(gr.sync_block):
    def __init__(self, path="", fmt="", realtime_rate=0.0):
        gr.sync_block.__init__(self, name="atsc3rx_capture_source", in_sig=None, out_sig=[np.complex64])
        self.fmt = (fmt or os.path.splitext(path)[1].lstrip(".") or "cs16").lower()
        self.fh = open(path, "rb")
        self.eof = False
        self.n = 0
        self.rate = float(realtime_rate)
        self.t0 = None

    def work(self, input_items, output_items):
        out = output_items[0]
        want = len(out)
        if self.eof:
            time.sleep(0.05)                       # quiet, not finished
            out[:min(want, 4096)] = 0
            return min(want, 4096)
        if self.fmt == "cf32":
            x = np.frombuffer(self.fh.read(want * 8), dtype=np.complex64)
        else:
            raw = np.frombuffer(self.fh.read(want * 4), dtype=np.int16)
            raw = raw[:(len(raw) // 2) * 2].astype(np.float32) / 32768.0
            x = (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)
        if len(x) == 0:
            self.eof = True
            return 0 if want == 0 else self.work(input_items, output_items)
        out[:len(x)] = x
        self.n += len(x)
        if self.rate:
            self.t0 = self.t0 or time.time()
            ahead = self.n / self.rate - (time.time() - self.t0)
            if ahead > 0:
                time.sleep(ahead)
        return len(x)
