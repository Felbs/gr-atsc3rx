# gr-atsc3rx handoff — 2026-09-21 (branch `main`, LOCAL ONLY: no GitHub repo, nothing pushed)

An ATSC 3.0 physical-layer RECEIVER for GNU Radio, ported stage by stage from an existing
working receiver (a separate project, not modified by this work).
State: **Python phase working end to end; GATE 1 PASSES.** Owner's decisions (2026-09-21):
name gr-atsc3rx, GPL-3.0-or-later, DEPEND on the reference receiver during the Python phases,
no outside contact yet.

## Read first
README.md -> docs/DESIGN.md (scope, tagged-stream/PDU decision, block list, three gates, phases)
-> docs/PRIOR_ART.md -> BUILD_LOG.md.

## Run it
```
set ATSC3_RECEIVER_DIR=<reference receiver root, the dir containing lab/>
python apps/atsc3_identify.py --capture captures/ref_a.cs16
python util/gate_bitexact.py captures/ref_a.cs16 --oracle captures/ref_a.oracle.dg
```
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

## Next
1. Gate 2 on the Ubuntu rig: build drmpeg/gr-atsc3, File-Sink its V&V flowgraphs at 6.912 MS/s, decode them.
2. A convolutional-interleaver / LDM capture through the `ldm` path (coded, not yet exercised).
3. Live: Soapy source -> chain -> UDP sink -> the reference receiver's transport + player.
4. First C++ stage: `frame_sync` front end (the hot path), then demapper, LDPC, de-interleavers.
5. QA files + CI; GRC example flowgraph + screenshots; rxtune recipe using the `dial`/`liveness` ports.
