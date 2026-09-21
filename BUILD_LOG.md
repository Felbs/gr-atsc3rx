# Build log

## 2026-09-21 — session 1: name check, prior-art refresh, design gate
- Spun out of the gr-rxtune work after the owner asked what a native GNU Radio ATSC 3.0 decoder
  would take. Owner: own project, not part of gr-rxtune; do the name check and prior art first.
- Names free on GitHub and CGRAN: gr-atsc3rx, gr-atsc3-rx, gr-nextgentv, gr-a3rx, gr-ngtv.
- Prior art refreshed against the August census: drmpeg/gr-atsc3 still TX-only (usable as a 6.912
  MS/s test source); awanga/usrp-atsc3 re-read at source level, still cannot decode (approximated
  LDPC tables, hard-decision L1); no DVB-T2 receiver exists in GNU Radio either; gr-dvbs2rx's
  tag-the-frame pattern and xdsopl LDPC port are the reusable pieces.
- `docs/DESIGN.md` written. STOPPED for approval.

## 2026-09-21 — session 2: Python phase built, gate 1 passes

Owner approved: name, GPL-3.0-or-later, depend on the reference receiver during Python phases.
- Scaffolded with gr_modtool (Python-only for now; lib/ and include/ return with the first C++ stage).
- Read-only map of the reference receiver's call path (front end, sniff -> plan, fast frame decoder vs
  LDM pipeline, ALP walker + IP reassembly, dump format). Blocks wrap it in the SAME order of operations
  as its live chain, including retuning the timing tracker to the signalled frame length.
- Blocks: frame_sync, frame_decoder, alp_decap, capture_source, dg_sink; `_core.py` bridge; GRC ymls;
  `apps/atsc3_identify.py`; `util/gate_bitexact.py`.
- Reference capture recorded with gr-rxtune's frontend at last night's gain verdict (12 s, integrity
  1.0016, no overflows); oracle from the unmodified reference receiver: 48 frames, 3552/3552 FEC, 4150 datagrams.
- **GATE 1: PASS.** 49 frames, 3626/3626 BCH-clean, all 4150 oracle datagrams byte-identical and in order,
  +1 trailing datagram (silence padding after EOF completes a last frame the reference run does not).
  31 s wall for 12 s of air = ~0.4x real time, as expected for Python message blocks.
- First run failed usefully: a metadata field was a dict and killed the decoder thread after frame 1
  (whose 89 datagrams already matched).

Not done / unverified: gate 2 (no C++ toolchain on this PC); the `ldm` decode path has never been run
here; no live-radio run of these blocks yet; no QA files, CI or GRC example yet; xdsopl Type A fit unverified.

## 2026-09-21 — session 3: LDM path gated, live air, GRC examples, QA

- **CTI/LDM path gated**: 25 s slice of a banked capture; oracle 3534/3534 FEC, 12285 datagrams; the chain
  reproduces it with the identical sha256, with and without worker processes. First attempt passed but took
  178 s: PMT serialisation of frame windows (0.46 s each) -> windows by reference. That exposed a silent
  frame-dropping cap (49 of 102 frames lost) -> back-pressure for files, counted drops for radios. MKL's 32
  threads throttled the decoder -> BLAS pinned in the bridge. A flush race lost the last 55 datagrams ->
  ordered draining. Now 39 s wall (0.64x); hybrid path 10 s for 11.9 s of air, sha256 identical too.
- **Live air**: four runs to get from "3 frames then nothing" to 12136/12136 (100.00 %) over 45 s:
  front end moved to its own thread; decoder -> frame sync `feedback` port implementing the reference
  receiver's weak-frame / bad-run supervisor; continuity breaks; 1 s source buffer; 2 s settle discard.
  Radio shared under the site lock throughout; every run stopped by its own timer, nothing killed.
- New: `dg_udp_sink` (multicast, TTL 0), `status_panel` (Qt, lazy import), `apps/atsc3_rx.py`,
  `util/make_grc.py` -> `examples/rx_capture.grc` + `rx_radio_qt.grc` (both compile with grcc),
  `util/grc_screenshot.py`, `util/run_qt_shot.py`, `docs/img/` (2 canvases + 1 live window, plan hidden).
- GRC flowgraph on live air: 13690/13690, 21234/21238, 21161/21164. Generated headless flowgraph verified
  with an independent multicast listener (3827 datagrams received).
- **QA**: 21 tests in 5 files against `qa_common.fake_receiver()` - no receiver, capture or radio needed.
  Writing them found two more defects (flush() waiting 10 min on a stopped thread; enum GRC param).
- `docs/TEST_REPORT.md` written: results, 12 defects, and what was NOT run.

Not done: gate 2; any C++; Linux build/CI; live CTI/LDM; soaks longer than 75 s; other radios.

## 2026-09-21 — session 4: public repo; television through the chain
- Owner said "give it a repo": dox gate 0/0 on tree + history grep clean -> github.com/Felbs/gr-atsc3rx (public).
- `examples/tv_bridge.py`: Soapy -> Frame Sync -> Frame Decoder -> `transport_sink` (the reference receiver's
  Transport + LiveWriter, fed the `bb` PDUs) -> live directory -> its `atsc3_play.py` -> mpv. First run wrote
  all four lanes but no picture: the reference player reads the lane list ONCE at launch, and a second attempt
  still raced live.json (written every few seconds, audio lanes listed first). The bridge now starts the players
  only once live.json lists a video lane that exists. Third run: 28712/28712 FEC, mpv played 1:36 of 720p60.
- Not run: `--audio` (AC-4 -> PCM) and the full A/V viewer on a bridge-written directory.

## 2026-09-21 — session 5: sound through the bridge
- `tv_bridge.py --tv [--sap]`: once a video lane is listed, starts the reference AC-4 decoder (one per language) and the
  full viewer (`atsc3_tv.py`: A/V sync, captions, player) on the bridge-written directory. `--helper-python` /
  ATSC3_PYTHON picks the interpreter those tools run under (they need the reference receiver's environment, not GNU Radio's).
- Run 1 (240 s): worked first time - VLC window with sound - but launching everything at once cost a re-acquisition.
  Fix: one helper every 4 s, below-normal priority. Run 2 (300 s): clean through launch; one reception event at ~107 s.
- Verified on the OUTPUT (the viewer's muxed TS), not on proxies: tracks, loudness, silence map, video decode errors.
- Status line now carries SNR and its minimum since the previous line, so the next dropout can be told from a fade.
