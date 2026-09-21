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


def __getattr__(name):
    # Qt is optional: the receiver runs headless. Import the panel only when it is asked for.
    if name == "status_panel":
        from .status_panel import status_panel as _cls   # (this also binds the SUBMODULE under the same name...)
        globals()["status_panel"] = _cls                 # ...so rebind the name to the class
        return _cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
