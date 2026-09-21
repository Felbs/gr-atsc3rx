#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Writes the example flowgraphs from one description, so they cannot drift apart.

  examples/rx_capture.grc   headless: a capture file in, datagrams out on UDP
  examples/rx_radio_qt.grc  Qt: a SoapySDR radio in, spectrum + status panel, datagrams on UDP

Nothing about any station is stored in either: the capture, the frequency, the antenna port
and the gains are flowgraph Parameters. Re-run after editing, then check both with grcc."""
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def blk(name_, id_, x, y, **params):
    return {"name": name_, "id": id_, "parameters": {k: str(v) for k, v in params.items()},
            "states": {"bus_sink": False, "bus_source": False, "bus_structure": None,
                       "coordinate": [x, y], "rotation": 0, "state": "enabled"}}


def chain(y, live):
    """The receiver itself: identical in both flowgraphs."""
    b = [
        blk("sync", "atsc3rx_frame_sync", 330, y, samp_rate="samp_rate", receiver_dir="receiver_dir",
            fe_threads=8, drop_when_behind=str(live), settle_sec=2.0 if live else 0, inline="False",
            comment="bootstrap -> L1 -> plan; one PDU per frame"),
        blk("dec", "atsc3rx_frame_decoder", 640, y, receiver_dir="receiver_dir", threads=16, iters=50, procs="procs",
            comment="OFDM, de-interleave, LDPC, BCH"),
        blk("alp", "atsc3rx_alp_decap", 960, y + 8, receiver_dir="receiver_dir", comment="link layer -> IP datagrams"),
        blk("udp", "atsc3rx_dg_udp_sink", 1230, y + 8, host="", interface="127.0.0.1", ttl=0,
            comment="as broadcast: multicast, TTL 0"),
    ]
    c = [["sync", "frames", "dec", "frames"], ["dec", "feedback", "sync", "feedback"],
         ["dec", "bb", "alp", "bb"], ["alp", "datagrams", "udp", "datagrams"]]
    return b, c


def options(name, title, qt, desc):
    return {"parameters": {"id": name, "title": title, "author": "gr-atsc3rx",
                           "generate_options": "qt_gui" if qt else "no_gui", "output_language": "python",
                           "category": "[GRC Hier Blocks]", "run": "True",
                           "run_options": "prompt" if qt else "run", "gen_cmake": "On", "description": desc},
            "states": {"bus_sink": False, "bus_source": False, "bus_structure": None,
                       "coordinate": [8, 8], "rotation": 0, "state": "enabled"}}


def common_params():
    return [
        blk("samp_rate", "variable", 230, 12, value="6.912e6", comment="ATSC 3.0's own rate: no resampler"),
        blk("receiver_dir", "parameter", 370, 12, label="Reference receiver (Python phase)", type="str",
            value='""', short_id="r", comment="empty = ATSC3_RECEIVER_DIR"),
        blk("procs", "parameter", 640, 12, label="Worker processes (CTI/LDM)", type="intx", value="4", short_id="p"),
    ]


def build_capture():
    y = 250
    b = common_params() + [
        blk("capture", "parameter", 860, 12, label="Capture file (.cs16 / .cf32)", type="str", value='"capture.cs16"',
            short_id="c"),
        blk("src", "atsc3rx_capture_source", 8, y, path="capture", fmt="", realtime_rate=0,
            comment="goes quiet, not finished, at end of file"),
        blk("show", "blocks_message_debug", 640, y + 190, en_uvec=False),
    ]
    cb, cc = chain(y, live=False)
    c = [["src", "0", "sync", "0"], ["sync", "plan", "show", "print"]] + cc
    return {"options": options("rx_capture", "ATSC 3.0 receiver: capture file to IP datagrams", False,
                               "Capture Source -> Frame Sync -> Frame Decoder -> ALP Decap -> UDP."),
            "blocks": b + cb, "connections": c,
            "metadata": {"file_format": 1, "grc_version": "3.10.12.0"}}, "rx_capture"


def build_radio():
    y = 470
    b = common_params() + [
        blk("freq", "parameter", 860, 12, label="Channel centre (Hz)", type="eng_float", value="600e6", short_id="f"),
        blk("driver", "parameter", 1040, 12, label="SoapySDR driver", type="str", value='"sdrplay"',
            short_id="d"),
        blk("antenna", "parameter", 1230, 12, label="Antenna port", type="str", value='""', short_id="a"),
        blk("gain", "parameter", 1390, 12, label="Overall gain (dB)", type="eng_float", value="30", short_id="g",
            comment="or tune the elements with gr-rxtune"),
        blk("src", "soapy_custom_source", 8, y - 20, driver="driver", type="fc32", nchan=1, dev_args='""',
            samp_rate="samp_rate", center_freq0="freq", bandwidth0="8e6", antenna0="antenna", gain0="gain",
            agc0=False, minoutbuf=str(1 << 23),
            comment="a second of buffer: start-up is the hungry moment"),
        blk("spectrum", "qtgui_freq_sink_x", 330, y - 330, type="complex", name='"carrier (baseband)"', fftsize=2048,
            fc=0, bw="samp_rate", average=0.05, gui_hint="0,0,1,1"),
        blk("waterfall", "qtgui_waterfall_sink_x", 330, y - 170, type="complex", name='"waterfall"', fftsize=2048,
            fc=0, bw="samp_rate", gui_hint="0,1,1,1"),
        blk("panel", "atsc3rx_status_panel", 960, y + 190, label="ATSC 3.0 receiver", show_plan="show_plan",
            gui_hint="1,0,1,2"),
        blk("show_plan", "parameter", 1040, 160, label="Show the PLP layout", type="intx", value="1", short_id="s"),
    ]
    cb, cc = chain(y, live=True)
    c = [["src", "0", "sync", "0"], ["src", "0", "spectrum", "0"], ["src", "0", "waterfall", "0"],
         ["sync", "plan", "panel", "plan"], ["dec", "quality", "panel", "quality"]] + cc
    return {"options": options("rx_radio_qt", "ATSC 3.0 receiver: radio to IP datagrams", True,
                               "A SoapySDR radio through the receiver, with the status panel."),
            "blocks": b + cb, "connections": c,
            "metadata": {"file_format": 1, "grc_version": "3.10.12.0"}}, "rx_radio_qt"


def main():
    for build in (build_capture, build_radio):
        doc, name = build()
        path = os.path.join(ROOT, "examples", name + ".grc")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False, default_flow_style=False)
        print("wrote", path)


if __name__ == "__main__":
    main()
