#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""ALP Decap: baseband payload in; IP datagrams out. This is where GNU Radio's job ends.

Walks the ATSC Link-layer Protocol (A/330) packets out of the baseband byte stream -
resumably, across frame boundaries - and reassembles the IPv4/UDP datagrams they carry.
Everything above this (ROUTE, MMTP, the service guide, audio and video) is not signal
processing and is left to whatever consumes the datagrams.

Python phase: the reference receiver's link-layer walker and IP reassembly, unchanged."""
import pmt
from gnuradio import gr

from . import _core


class alp_decap(gr.basic_block):
    """
    in : message 'bb' (from Frame Decoder)
    out: message 'datagrams'  PDU meta {src, dst, sport, dport}  data = UDP payload (u8vector)
    """

    def __init__(self, receiver_dir=""):
        gr.basic_block.__init__(self, name="atsc3rx_alp_decap", in_sig=None, out_sig=None)
        self.rx = _core.load(receiver_dir or None)
        self.walker = self.rx.ST.StreamAlpWalker()
        self.ip = self.rx.R7.IpReasm()
        self.n_datagrams = 0
        self.other_types = 0
        self.message_port_register_in(pmt.intern("bb"))
        self.set_msg_handler(pmt.intern("bb"), self.on_bb)
        self.message_port_register_out(pmt.intern("datagrams"))

    @staticmethod
    def _ip(b):
        return ".".join(str(x) for x in bytes(b))

    def _emit(self, packets):
        for ptype, payload in packets:
            if ptype != 0:                      # 0 = IPv4; signalling and extensions are counted, not decoded
                self.other_types += 1
                continue
            for src, dst, sp, dp, pay in self.ip.feed(payload):
                self.n_datagrams += 1
                meta = {"src": self._ip(src), "dst": self._ip(dst), "sport": int(sp), "dport": int(dp)}
                self.message_port_pub(pmt.intern("datagrams"), pmt.cons(
                    pmt.to_pmt(meta), pmt.init_u8vector(len(pay), list(pay))))

    def on_bb(self, msg):
        meta = pmt.to_python(pmt.car(msg))
        stream = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
        self._emit(self.walker.feed(stream, list(meta.get("bounds", []))))

    def flush(self):
        self._emit(self.walker.pump(final=True))
