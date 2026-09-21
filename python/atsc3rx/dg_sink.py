#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Datagram File Sink: the acceptance-test sink.

Writes every datagram as  <4s src><4s dst><H sport><H dport><I length> + payload  (little
endian) - the reference receiver's own dump format, so a file written here can be compared
with one written there byte for byte, and hashed."""
import hashlib
import struct

import pmt
from gnuradio import gr

REC = struct.Struct("<4s4sHHI")


class dg_sink(gr.basic_block):
    def __init__(self, path=""):
        gr.basic_block.__init__(self, name="atsc3rx_dg_sink", in_sig=None, out_sig=None)
        self.path = path
        self.fh = open(path, "wb") if path else None
        self.sha = hashlib.sha256()
        self.n = 0
        self.nbytes = 0
        self.message_port_register_in(pmt.intern("datagrams"))
        self.set_msg_handler(pmt.intern("datagrams"), self.on_dg)

    def on_dg(self, msg):
        meta = pmt.to_python(pmt.car(msg))
        pay = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
        rec = REC.pack(bytes(int(x) for x in meta["src"].split(".")),
                       bytes(int(x) for x in meta["dst"].split(".")),
                       int(meta["sport"]), int(meta["dport"]), len(pay)) + pay
        self.sha.update(rec)
        self.n += 1
        self.nbytes += len(rec)
        if self.fh is not None:
            self.fh.write(rec)

    def close(self):
        if self.fh is not None:
            self.fh.close()
            self.fh = None
        return self.sha.hexdigest()

    def stop(self):
        self.close()
        return True
