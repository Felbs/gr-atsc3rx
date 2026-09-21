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
