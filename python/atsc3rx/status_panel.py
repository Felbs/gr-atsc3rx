#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Status Panel (Qt): what the receiver knows, at a glance.

The plan the broadcaster signals; per-frame FEC health as a strip (one cell per frame,
green = every block BCH-clean); SNR off the known dummy cells as a trace; totals."""
import collections
import threading

import pmt
from gnuradio import gr
from PyQt5 import QtCore, QtGui, QtWidgets

HISTORY = 240             # frames kept on screen: about a minute


class _Strip(QtWidgets.QWidget):
    def __init__(self, panel):
        super().__init__()
        self.p = panel
        self.setMinimumHeight(150)

    def paintEvent(self, _):
        qp = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        qp.fillRect(0, 0, w, h, QtGui.QColor(18, 20, 24))
        with self.p.lock:
            hist = list(self.p.hist)
        if not hist:
            qp.setPen(QtGui.QColor(140, 140, 140))
            qp.drawText(10, 24, "waiting for frames")
            return
        cw = max(2.0, w / float(HISTORY))
        bar_h = 26
        # FEC strip
        for i, (frac, snr) in enumerate(hist):
            x = int(i * cw)
            if frac is None:
                col = QtGui.QColor(70, 70, 70)
            elif frac >= 1.0:
                col = QtGui.QColor(40, 170, 90)
            elif frac >= 0.5:
                col = QtGui.QColor(215, 165, 40)
            else:
                col = QtGui.QColor(200, 60, 50)
            qp.fillRect(x, 4, max(1, int(cw) - 1), bar_h, col)
        # SNR trace
        top, bot = bar_h + 14, h - 6
        lo, hi = self.p.snr_lo, self.p.snr_hi
        qp.setPen(QtGui.QColor(60, 64, 72))
        for db in range(int(lo), int(hi) + 1, 10):
            y = bot - (db - lo) / (hi - lo) * (bot - top)
            qp.drawLine(0, int(y), w, int(y))
            qp.drawText(4, int(y) - 2, f"{db} dB")
        qp.setPen(QtGui.QPen(QtGui.QColor(90, 170, 255), 2))
        prev = None
        for i, (_, snr) in enumerate(hist):
            if snr is None:
                prev = None
                continue
            y = bot - (min(max(snr, lo), hi) - lo) / (hi - lo) * (bot - top)
            pt = QtCore.QPointF(i * cw + cw / 2, y)
            if prev is not None:
                qp.drawLine(prev, pt)
            prev = pt


class status_panel(gr.basic_block, QtWidgets.QWidget):
    """
    in : message 'plan' (Frame Sync), 'quality' (Frame Decoder)
    """

    def __init__(self, label="ATSC 3.0", show_plan=True, snr_lo=0.0, snr_hi=35.0, parent=None):
        gr.basic_block.__init__(self, name="atsc3rx_status_panel", in_sig=None, out_sig=None)
        QtWidgets.QWidget.__init__(self, parent)
        self.lock = threading.Lock()
        self.hist = collections.deque(maxlen=HISTORY)
        self.snr_lo, self.snr_hi = float(snr_lo), float(snr_hi)
        self.show_plan = bool(show_plan)
        self.plan_text = "no plan yet: looking for a bootstrap"
        self.frames = self.fec = self.clean = 0
        self.last_snr = None

        lay = QtWidgets.QVBoxLayout(self)
        self.title = QtWidgets.QLabel(f"<b>{label}</b>")
        self.totals = QtWidgets.QLabel("")
        self.totals.setStyleSheet("font-size: 15pt;")
        self.plan = QtWidgets.QLabel(self.plan_text)
        self.plan.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.plan.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.strip = _Strip(self)
        for wdg in (self.title, self.totals, self.strip, self.plan):
            lay.addWidget(wdg)
        lay.setStretchFactor(self.strip, 1)

        self.message_port_register_in(pmt.intern("plan"))
        self.set_msg_handler(pmt.intern("plan"), self.on_plan)
        self.message_port_register_in(pmt.intern("quality"))
        self.set_msg_handler(pmt.intern("quality"), self.on_quality)
        self.timer = QtCore.QTimer(self)               # handlers run on GNU Radio threads; paint on Qt's
        self.timer.timeout.connect(self.refresh)
        self.timer.start(250)

    def on_plan(self, msg):
        d = pmt.to_python(msg)
        if isinstance(d, dict):
            with self.lock:
                if self.show_plan:
                    self.plan_text = str(d.get("describe", ""))
                else:
                    path = "convolutional interleaver / LDM" if d.get("mode") == "ldm" else "hybrid interleaver"
                    self.plan_text = f"plan received ({path} path); what this station signals is hidden (Show plan = No)"

    def on_quality(self, msg):
        d = pmt.to_python(msg)
        if not isinstance(d, dict):
            return
        n, ok = int(d.get("n_fec", 0)), int(d.get("bch_ok", 0))
        snr = d.get("snr_db")
        with self.lock:
            self.frames += 1
            self.fec += n
            self.clean += ok
            self.last_snr = snr if snr is not None else self.last_snr
            self.hist.append(((ok / n) if n else None, snr))

    def refresh(self):
        with self.lock:
            frames, fec, clean, snr, plan = self.frames, self.fec, self.clean, self.last_snr, self.plan_text
        pct = f"{100.0 * clean / fec:.2f}%" if fec else "-"
        s = f"{snr:.1f} dB" if isinstance(snr, (int, float)) else "-"
        self.totals.setText(f"frames {frames}   FEC blocks BCH-clean {clean}/{fec} ({pct})   SNR {s}")
        self.plan.setText(plan)
        self.strip.update()
