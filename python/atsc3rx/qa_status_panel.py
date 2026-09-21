#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Status Panel QA. Runs on the 'offscreen' Qt platform so it needs no display.

Proves: messages arriving on scheduler threads reach the widgets; the panel paints;
'Show plan = No' really withholds what a station signals."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pmt  # noqa: E402
from gnuradio import gr_unittest  # noqa: E402
from PyQt5 import QtWidgets  # noqa: E402

from qa_common import atsc3rx  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
SECRET = "PLP7(L0 16QAM 5/15)"          # invented, not any station


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.01)


def feed(panel):
    panel.on_plan(pmt.to_pmt({"mode": "m9", "describe": "sniffed: FFT 8K\n    layers: " + SECRET, "spec_json": "{}"}))
    for ok in (74, 74, 30, 0):
        panel.on_quality(pmt.to_pmt({"frame": 0, "n_fec": 74, "bch_ok": ok, "converged": ok, "snr_db": 20.5}))
    panel.on_quality(pmt.to_pmt({"frame": 0, "n_fec": 0, "bch_ok": 0, "converged": 0}))     # CTI fill: no blocks yet


class qa_status_panel(gr_unittest.TestCase):

    def test_001_lazy_import_gives_the_class_every_time(self):
        self.assertTrue(isinstance(atsc3rx.status_panel, type))
        self.assertTrue(isinstance(atsc3rx.status_panel, type))      # the second access once returned the MODULE

    def test_002_totals_and_painting(self):
        p = atsc3rx.status_panel(label="qa")
        p.resize(800, 400)
        p.show()
        feed(p)
        pump(0.6)
        self.assertIn("178/296", p.totals.text())
        self.assertIn("20.5 dB", p.totals.text())
        self.assertIn(SECRET, p.plan.text())
        self.assertEqual(len(p.hist), 5)
        self.assertFalse(p.grab().isNull())
        p.timer.stop()
        p.close()

    def test_003_show_plan_off_withholds_what_the_station_signals(self):
        p = atsc3rx.status_panel(label="qa", show_plan=False)
        p.show()
        feed(p)
        pump(0.6)
        self.assertNotIn(SECRET, p.plan.text())
        self.assertNotIn("FFT", p.plan.text())
        self.assertIn("hidden", p.plan.text())
        p.timer.stop()
        p.close()


if __name__ == '__main__':
    gr_unittest.run(qa_status_panel)
