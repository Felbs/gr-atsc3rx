#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Datagram UDP Sink: put the recovered IP datagrams back on a network.

Each datagram goes out as UDP to the destination address and port it was broadcast with
(ATSC 3.0 services are multicast groups), so anything that can join a multicast group -
a ROUTE/MMTP stack, a packet capture, a player - sees the broadcast as if it had a tuner.

TTL defaults to 0: the packets never leave this machine. Raising it re-transmits a
broadcast onto your network; that is your decision and your responsibility."""
import collections
import socket

import pmt
from gnuradio import gr


class dg_udp_sink(gr.basic_block):
    """
    in : message 'datagrams' (from ALP Decap)
    host = ''  -> send to each datagram's own destination address (multicast)
    host = 'x' -> send everything to x, keeping each datagram's destination port
    """

    def __init__(self, host="", interface="127.0.0.1", ttl=0):
        gr.basic_block.__init__(self, name="atsc3rx_dg_udp_sink", in_sig=None, out_sig=None)
        self.host = host
        self.n = self.n_err = 0
        self.flows = collections.Counter()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, int(ttl))
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        if interface:
            self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
        self.message_port_register_in(pmt.intern("datagrams"))
        self.set_msg_handler(pmt.intern("datagrams"), self.on_dg)

    def on_dg(self, msg):
        meta = pmt.to_python(pmt.car(msg))
        pay = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
        dst, port = meta.get("dst", ""), int(meta.get("dport", 0))
        self.flows[(dst, port)] += 1
        try:
            self.sock.sendto(pay, (self.host or dst, port))
            self.n += 1
        except OSError:
            self.n_err += 1

    def stop(self):
        try:
            self.sock.close()
        except OSError:
            pass
        return True
