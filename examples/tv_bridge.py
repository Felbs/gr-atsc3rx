#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Watch television through the GNU Radio receiver.

  radio -> [GNU Radio: Frame Sync -> Frame Decoder] -> baseband -> reference receiver's
           transport (MMTP -> fragmented MP4 lanes) -> a growing live directory -> its players

GNU Radio owns the radio and the whole physical layer. Everything above IP - MMTP, the media
lanes, AC-4 audio, the player - is the reference receiver's, unchanged: this project stops at
IP datagrams on purpose, and this example is the join.

  tv_bridge.py --soapy "driver=..." --freq HZ [--antenna NAME] [--gain ELEMENT=VALUE ...]
               --live-dir DIR [--seconds N] [--tv | --play] [--sap]

--tv     picture AND sound: starts the reference receiver's AC-4 decoder (tools/atsc3_audio.py) and its
         full viewer (tools/atsc3_tv.py: A/V sync, captions, player) on DIR. About 30-60 s behind the air.
--sap    with --tv: also decode the second audio programme.
--play   picture only, a few seconds behind: tools/atsc3_play.py.
--helper-python  the interpreter the reference receiver's tools run under (default ATSC3_PYTHON, else this one).
Clear (unencrypted) services only. It never decrypts anything.
If the radio is shared, RXTUNE_LOCK (see gr-rxtune) names a site lock module and is honoured."""
import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from gnuradio import atsc3rx
except ImportError:
    import _devpath  # noqa: F401
    from gnuradio import atsc3rx

import pmt  # noqa: E402
from gnuradio import gr, soapy  # noqa: E402
from gnuradio.atsc3rx import _core  # noqa: E402


class transport_sink(gr.basic_block):
    """Baseband PDUs in; the reference receiver's transport writes media lanes to a live directory."""

    def __init__(self, receiver_dir, live_dir):
        gr.basic_block.__init__(self, name="transport_sink", in_sig=None, out_sig=None)
        rx = _core.load(receiver_dir or None)
        import m11_watch as W11                                  # the reference receiver's live writer
        self.tr = rx.ST.Transport(probe=64, want=(b"vide", b"soun", b"subt", b"text"))
        self.live = W11.LiveWriter(live_dir)
        self.n_seg = 0
        self.snr, self.snr_min = None, None                      # so a glitch can be told from a fade afterwards
        self.message_port_register_in(pmt.intern("bb"))
        self.set_msg_handler(pmt.intern("bb"), self.on_bb)
        self.message_port_register_in(pmt.intern("dial"))
        self.set_msg_handler(pmt.intern("dial"), self.on_dial)

    def on_dial(self, msg):
        self.snr = pmt.to_double(pmt.cdr(msg))
        self.snr_min = self.snr if self.snr_min is None else min(self.snr_min, self.snr)

    def on_bb(self, msg):
        meta = pmt.to_python(pmt.car(msg))
        stream = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
        for seg in self.tr.feed(stream, list(meta.get("bounds", []))):
            self.live.write(self.tr.init, seg)
            self.n_seg += 1

    def stop(self):
        try:
            self.live.close()
        except Exception:                                        # noqa: BLE001
            pass
        return True


def video_lane_listed(live_dir):
    """True once live.json (written every few seconds) lists a video lane that exists on disk."""
    try:
        with open(os.path.join(live_dir, "live.json")) as fh:
            lanes = json.load(fh).get("lanes", {})
    except (OSError, ValueError):
        return False
    return any(ln.get("kind") == "video" and os.path.exists(ln.get("path", "")) for ln in lanes.values())


def second_audio_pid(live_dir):
    try:
        with open(os.path.join(live_dir, "live.json")) as fh:
            lanes = json.load(fh).get("lanes", {})
    except (OSError, ValueError):
        return None
    pids = sorted(int(ln["pid"]) for ln in lanes.values() if ln.get("kind") == "audio" and "pid" in ln)
    return pids[1] if len(pids) > 1 else None


def site_lock(owner):
    if not os.environ.get("RXTUNE_LOCK"):
        return contextlib.nullcontext(None)
    from rxtune import lock
    return lock.from_env(owner=owner, priority=60)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--soapy", required=True)
    ap.add_argument("--freq", type=float, required=True)
    ap.add_argument("--antenna")
    ap.add_argument("--gain", nargs="*", default=[], metavar="ELEMENT=VALUE")
    ap.add_argument("--rate", type=float, default=6.912e6)
    ap.add_argument("--live-dir", required=True)
    ap.add_argument("--seconds", type=float, default=0.0)
    ap.add_argument("--receiver", default=os.environ.get("ATSC3_RECEIVER_DIR", ""))
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--play", action="store_true")
    ap.add_argument("--player", default="mpv")
    ap.add_argument("--player-args", default=None)
    ap.add_argument("--lag", type=float, default=10.0)
    ap.add_argument("--tv", action="store_true")
    ap.add_argument("--sap", action="store_true")
    ap.add_argument("--sap-pid", type=int, default=None, help="--sap: the lane's pid (default: the second audio lane)")
    ap.add_argument("--tv-args", default=None, help="extra arguments for atsc3_tv.py")
    ap.add_argument("--helper-python", default=os.environ.get("ATSC3_PYTHON") or sys.executable)
    a = ap.parse_args()
    if not a.receiver:
        ap.error("--receiver or ATSC3_RECEIVER_DIR")
    live_dir = os.path.abspath(a.live_dir)
    os.makedirs(live_dir, exist_ok=True)

    helpers = []
    with site_lock("atsc3rx-tv") as lk:
        tb = gr.top_block()
        src = soapy.source(a.soapy, "fc32", 1, "", "", [""], [""])
        src.set_sample_rate(0, a.rate)
        src.set_bandwidth(0, 8e6)
        src.set_frequency(0, a.freq)
        src.set_gain_mode(0, False)
        if a.antenna:
            src.set_antenna(0, a.antenna)
        for item in a.gain:
            name, val = item.split("=")
            src.set_gain(0, name, float(val))
        src.set_min_output_buffer(1 << 23)
        sync = atsc3rx.frame_sync(a.rate, a.receiver, drop_when_behind=True, settle_sec=2.0)
        dec = atsc3rx.frame_decoder(a.receiver, threads=a.threads, procs=a.procs)
        tv = transport_sink(a.receiver, live_dir)
        tb.connect(src, sync)
        tb.msg_connect(sync, "frames", dec, "frames")
        tb.msg_connect(dec, "feedback", sync, "feedback")
        tb.msg_connect(dec, "bb", tv, "bb")
        tb.msg_connect(dec, "dial", tv, "dial")

        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, lambda *_: stop.set())
        t0 = time.time()
        tb.start()
        tools = os.path.join(a.receiver, "tools")
        started = False
        next_launch = 0.0

        pending = []                       # (name, script, args): launched one at a time, a few seconds apart

        def spawn(name, script, *args):
            pending.append((name, script, args))

        def launch_next():
            # The viewer must never starve the radio: launching a decoder, a muxer and a player in the same
            # second cost a re-acquisition here. Below-normal priority, and one process at a time.
            name, script, args = pending.pop(0)
            cmd = [a.helper_python, "-u", os.path.join(tools, script), "--live-dir", live_dir] + [str(x) for x in args]
            kw = {"creationflags": subprocess.BELOW_NORMAL_PRIORITY_CLASS} if os.name == "nt" else                  {"preexec_fn": lambda: os.nice(5)}
            helpers.append(subprocess.Popen(cmd, cwd=a.receiver, stderr=subprocess.STDOUT,
                                            stdout=open(os.path.join(live_dir, name + ".log"), "ab"), **kw))

        def start_helpers():
            # the reference tools read the lane list ONCE, at launch: start them when there is a video lane
            if a.tv:
                spawn("audio", "atsc3_audio.py", "--wait", 600, "--channels", 2)
                if a.sap:
                    pid = a.sap_pid or second_audio_pid(live_dir)
                    if pid is not None:
                        spawn("audio_sap", "atsc3_audio.py", "--pid", pid, "--element", "pair", "--channels", 2,
                              "--wait", 600, "--out", os.path.join(live_dir, "live_audio_spa.wav"))
                extra = a.tv_args.split() if a.tv_args else []
                if a.seconds:
                    extra += ["--max-seconds", a.seconds + 90]
                spawn("tv", "atsc3_tv.py", *extra)
            elif a.play:
                extra = ["--player-args", a.player_args] if a.player_args else []
                spawn("play", "atsc3_play.py", "--lag", a.lag, "--player", a.player, *extra)

        why = "interrupted"
        while not stop.is_set():
            time.sleep(1.0)
            if lk is not None:
                lk.heartbeat()
                y = lk.should_yield()
                if y:
                    why = f"yielded the radio: {y}"
                    break
            if not started and video_lane_listed(live_dir):
                started = True
                start_helpers()
            if pending and time.time() >= next_launch:
                launch_next()
                next_launch = time.time() + 4.0
            if int(time.time() - t0) % 5 == 0:
                print(f"[{time.time() - t0:6.1f}s] frames {dec.n_frames}  FEC {dec.n_bch}/{dec.n_fec} BCH-clean  "
                      f"re-acq {sync.n_reacquire}  breaks {sync.n_breaks}  dropped {sync.n_dropped}  media segments {tv.n_seg}  "
                      f"live {tv.live.bytes / 1e6:.1f} MB  SNR {tv.snr if tv.snr is not None else float('nan'):.1f} dB "
                      f"(min since last line {tv.snr_min if tv.snr_min is not None else float('nan'):.1f})", flush=True)
                tv.snr_min = None
            if a.seconds and time.time() - t0 >= a.seconds:
                why = f"{a.seconds:.0f} s elapsed"
                break
        tb.stop()
        tb.wait()
    print(f"stopped: {why}; {tv.n_seg} media segments, FEC {dec.n_bch}/{dec.n_fec}")
    # the viewer tails files: let it drain what was written (it stops itself with --max-seconds), then
    # ask everything that is left to go. None of these hold the radio.
    if helpers:
        try:
            helpers[-1].wait(timeout=100.0 if a.tv else max(5.0, a.lag + 5.0))
        except subprocess.TimeoutExpired:
            pass
    for p in helpers:
        if p.poll() is None:
            p.terminate()
    return 0 if tv.n_seg else 1


if __name__ == "__main__":
    sys.exit(main())
