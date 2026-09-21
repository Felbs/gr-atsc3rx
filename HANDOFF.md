# gr-atsc3rx handoff — 2026-09-21 (branch `main`, LOCAL ONLY: no GitHub repo, nothing pushed)

An ATSC 3.0 physical-layer RECEIVER for GNU Radio, ported stage by stage from an existing
working receiver (a separate project, not modified by this work).
State: **Python phase working end to end on captures AND live air; GATE 1 PASSES byte-identical on
both decode paths; 21 QA tests; GRC examples + screenshots.** Owner's decisions (2026-09-21):
name gr-atsc3rx, GPL-3.0-or-later, DEPEND on the reference receiver during the Python phases,
no outside contact yet.

## Read first
README.md -> docs/TEST_REPORT.md (what was and was NOT run) -> docs/DESIGN.md (scope, tagged-stream/PDU decision, block list, three gates, phases)
-> docs/PRIOR_ART.md -> BUILD_LOG.md.

## Run it
```
set ATSC3_RECEIVER_DIR=<reference receiver root, the dir containing lab/>
python apps/atsc3_identify.py --capture captures/ref_a.cs16
python util/gate_bitexact.py captures/ref_a.cs16 --oracle captures/ref_a.oracle.dg --threads 16
python util/gate_bitexact.py captures/ref_b.cs16 --oracle captures/ref_b.oracle.dg --threads 16 --procs 4
python apps/atsc3_rx.py --soapy driver=sdrplay --freq <Hz> --antenna "<port>" --gain RFGR=<n> IFGR=<n> --seconds 45 --udp
python -X faulthandler python/atsc3rx/qa_<name>.py        (5 files, 21 tests, no receiver needed)
python util/make_grc.py ; grcc -o build/grc examples/*.grc ; python util/grc_screenshot.py X.grc X.png
python util/run_qt_shot.py build/grc/rx_radio_qt.py --set freq=.. --set "antenna=.." --set show_plan=0 \
       --call "src.set_gain(0,'IFGR',<n>)" --png out.png
```
ref_a = hybrid-interleaver multiplex (12 s), ref_b = convolutional-interleaver/LDM multiplex (25 s slice of a
banked capture). Shared radio: set RXTUNE_LOCK to the site lock module and put gr-rxtune's src/ on PYTHONPATH.
grc_screenshot needs radioconda's Library/bin on PATH (GTK DLLs).
Use a GNU Radio 3.10 Python. `captures/` is git-ignored and must stay that way. Make an oracle
with the reference receiver: `python -m atsc3 watch --capture F --rate 6.912e6 --player none --dump-dg F.dg`.

## Rules
- No repo creation, push, or contact with anyone (incl. the gr-atsc3 author) without the owner's word.
- Dox gate before any push. NO captured broadcast content, channel numbers, PLP layouts of real
  stations, or locations in the tree. `atsc3_identify` output of a real carrier is station data.
- Never implement decryption.
- A stage counts as ported only when `util/gate_bitexact.py` still passes.
- GPU stays optional: `_core.load()` sets M9_NO_TORCH=1 so the flowgraph process never imports torch.

## Traps
- This PC has GR_PREFIX / GRC_BLOCKS_PATH pointing at another GNU Radio: override for gr_modtool/grcc.
- A flowgraph that ENDS at end of file drops the frames still in flight in message blocks: use
  Capture Source (goes quiet, sets `.eof`) and poll, as the gate does.
- The reference decoder's per-frame `diag["dummy"]` is a dict, not a number.
- The Python gateway finds message handlers by NAME: no lambdas.

## Traps found in session 3
- A ~1.7 M-sample frame window costs 0.46 s through a PMT vector. Windows go BY REFERENCE in-process
  (`_core.park_window/claim_window`); a file WAITS when 8 are unclaimed, a radio DROPS whole frames and counts.
- MKL defaults to 32 threads here and throttles the decoder 4x: `_core.load()` pins BLAS to 1.
- Never do acquisition inside `work()`: the radio overflows. Frame Sync's `work()` only queues.
- Messages are asynchronous: flush stage N+1 only after it has RECEIVED everything stage N published.
- An RSPdx needs ~2 s after stream start before its samples are worth locking to (`settle_sec`).
- GRC enum params given an expression silently fall back to the default: use dtype bool.
- A Python block must be kept referenced while its flowgraph runs (access violation otherwise).
- A finite stock source ENDS the flowgraph under message blocks: tests use Capture Source.

## Traps found in sessions 5-6
- FIXED UPSTREAM 9/21 (reference receiver branch fix/ce-norm-derotate): its E60 CE normalisation averaged RAW
  pilots; a residual CFO (~350 deg/frame) collapsed the mean -> 0/74 at a healthy SNR. (Not an echo, as first thought.)
  A reference checkout WITHOUT that fix still has the bug: the rescue below is what protects this chain from it. `frame_decoder(rescue=True)` retries such frames with `margin={"ce_w": 0}`.
  Local repro + notes: runs/fastpath_bug/ (NOT in git). The reference's GPU and exact-CPU paths are unaffected,
  so an oracle made on a GPU box can legitimately beat this chain until that is fixed upstream.
- A continuity break must discard the whole queue, or the slow re-acquisition overflows it again, forever.
- The reference tools read live.json ONCE at launch; start them only when a video lane is listed. If the viewer
  has to be terminated, close the player recorded in _tv/player.pid or it stays open, paused, on a dead stream.
- Launch helper processes one at a time at below-normal priority: a burst starves the radio thread.
- This PC bugchecks under sustained load (several different stop codes this month). Long tests must be detached
  and must leave their evidence on disk; never assume a run will reach its own clean-up.

## Next
1. Gate 2 on the Ubuntu rig: build drmpeg/gr-atsc3, File-Sink its V&V flowgraphs at 6.912 MS/s, decode them.
2. First C++ stage. The profile says where: the CTI/LDM path is 0.64x here vs 1.8x in the reference, all of
   it single-Python-process overhead; the front end (resample, de-rotate, notch) is the natural first port.
3. Linux build + CI running the 21 QA tests (they need no receiver).
4. Live CTI/LDM multiplex (needs the speed from 2); longer live soaks; a second radio type.
5. gr-rxtune example using this chain's `dial` + `liveness` ports directly (no capture-per-cell).
6. Feed the UDP output to the reference receiver's transport + player (TV through GNU Radio, natively).
