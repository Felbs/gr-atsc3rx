#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
import hashlib
import os
import socket
import struct
import tempfile
import time

import numpy as np
import pmt
from gnuradio import blocks, gr, gr_unittest

from qa_common import atsc3rx, fake_receiver, sink


def dg(src, dst, sp, dp, pay):
    meta = {"src": src, "dst": dst, "sport": sp, "dport": dp}
    return pmt.cons(pmt.to_pmt(meta), pmt.init_u8vector(len(pay), list(pay)))


class qa_sinks(gr_unittest.TestCase):

    def test_001_datagram_file_is_the_reference_dump_format(self):
        path = os.path.join(tempfile.mkdtemp(), "out.dg")
        s = atsc3rx.dg_sink(path)
        s.on_dg(dg("10.0.0.1", "239.1.2.3", 1000, 2000, b"hello"))
        s.on_dg(dg("10.0.0.1", "239.1.2.3", 1000, 2001, b""))
        sha = s.close()
        raw = open(path, "rb").read()
        want = (struct.pack("<4s4sHHI", bytes([10, 0, 0, 1]), bytes([239, 1, 2, 3]), 1000, 2000, 5) + b"hello"
                + struct.pack("<4s4sHHI", bytes([10, 0, 0, 1]), bytes([239, 1, 2, 3]), 1000, 2001, 0))
        self.assertEqual(raw, want)
        self.assertEqual(sha, hashlib.sha256(want).hexdigest())

    def test_002_udp_sink_delivers_to_a_named_host_keeping_the_port(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(3.0)
        port = rx.getsockname()[1]
        s = atsc3rx.dg_udp_sink(host="127.0.0.1")
        s.on_dg(dg("10.0.0.1", "239.1.2.3", 1000, port, b"nextgen"))
        data, _ = rx.recvfrom(2048)
        rx.close()
        s.stop()
        self.assertEqual(data, b"nextgen")
        self.assertEqual(s.flows[("239.1.2.3", port)], 1)

    def test_004_alp_decap_emits_ipv4_only_and_flush_releases_the_tail(self):
        root, _ = fake_receiver()
        alp = atsc3rx.alp_decap(root)
        out = sink("datagrams")
        tb = gr.top_block()
        tb.msg_connect(alp, "datagrams", out, "datagrams")
        tb.start()
        bb = bytes([0, 1, 9, 9,   4, 2, 9, 9,   0, 3, 9, 9,   0, 4])          # IPv4, signalling, IPv4, partial
        alp.to_basic_block()._post(pmt.intern("bb"), pmt.cons(
            pmt.to_pmt({"frame": 0, "bounds": [0]}), pmt.init_u8vector(len(bb), list(bb))))
        time.sleep(0.5)
        self.assertEqual((alp.n_bb, alp.n_datagrams, alp.other_types), (1, 2, 1))
        alp.flush()
        time.sleep(0.3)
        tb.stop()
        tb.wait()
        self.assertEqual(alp.n_datagrams, 3)
        metas = [pmt.to_python(pmt.car(m)) for m in out.got["datagrams"]]
        self.assertEqual([m["dport"] for m in metas], [2001, 2003, 2004])
        self.assertEqual(metas[0]["dst"], "239.1.2.3")
        self.assertEqual(bytes(pmt.u8vector_elements(pmt.cdr(out.got["datagrams"][2]))), bytes([0, 4]))

    def test_003_capture_source_reads_cs16_and_goes_quiet_not_finished(self):
        path = os.path.join(tempfile.mkdtemp(), "c.cs16")
        np.array([[16384, -16384], [0, 32767], [-32768, 0]], dtype=np.int16).tofile(path)
        src = atsc3rx.capture_source(path)
        snk = blocks.vector_sink_c()
        tb = gr.top_block()
        tb.connect(src, snk)
        tb.start()
        time.sleep(0.6)
        self.assertTrue(src.eof)                             # ...and the flowgraph is still running
        tb.stop()
        tb.wait()
        got = np.array(snk.data())
        self.assertEqual(len(got), 3)                        # no padding after the end
        self.assertAlmostEqual(got[0], 0.5 - 0.5j, places=6)
        self.assertAlmostEqual(got[2], -1.0 + 0j, places=6)


if __name__ == '__main__':
    gr_unittest.run(qa_sinks)
