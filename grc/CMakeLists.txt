# SPDX-License-Identifier: GPL-3.0-or-later
install(FILES
    atsc3rx_frame_sync.block.yml
    atsc3rx_frame_decoder.block.yml
    atsc3rx_alp_decap.block.yml
    atsc3rx_dg_sink.block.yml
    atsc3rx_dg_udp_sink.block.yml
    atsc3rx_capture_source.block.yml DESTINATION share/gnuradio/grc/blocks)
