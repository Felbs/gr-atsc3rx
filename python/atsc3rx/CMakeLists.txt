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
    status_panel.py
    capture_source.py DESTINATION ${GR_PYTHON_DIR}/gnuradio/atsc3rx)

# QA needs no reference receiver, no capture and no radio: see qa_common.fake_receiver.
# The decode itself is held bit-exact against the reference receiver by util/gate_bitexact.py.
include(GrTest)
set(GR_TEST_TARGET_DEPS gnuradio-atsc3rx)
foreach(qa qa_core qa_sinks qa_frame_sync qa_frame_decoder qa_status_panel)
    GR_ADD_TEST(${qa} ${PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/${qa}.py)
endforeach()
