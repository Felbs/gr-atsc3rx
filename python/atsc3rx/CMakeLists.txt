# SPDX-License-Identifier: GPL-3.0-or-later
include(GrPython)
if(NOT PYTHONINTERP_FOUND)
    return()
endif()

gr_python_install(FILES __init__.py
    _core.py
    frame_sync.py
    frame_decoder.py
    alp_decap.py
    dg_sink.py
    dg_udp_sink.py
    capture_source.py DESTINATION ${GR_PYTHON_DIR}/gnuradio/atsc3rx)
