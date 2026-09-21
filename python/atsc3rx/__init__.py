#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
'''
gr-atsc3rx: an ATSC 3.0 (NextGen TV) physical-layer receiver for GNU Radio.
Complex baseband in, IP datagrams out. It never decrypts anything.
'''
try:
    from .atsc3rx_python import *          # no compiled bindings yet (Python phase)
except ModuleNotFoundError:
    pass

from .frame_sync import frame_sync
from .frame_decoder import frame_decoder
from .alp_decap import alp_decap
from .dg_sink import dg_sink
from .capture_source import capture_source
from .dg_udp_sink import dg_udp_sink
