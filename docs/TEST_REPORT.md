# gr-atsc3rx test report

2026-09-21 · GNU Radio 3.10.12 (radioconda, Python 3.12, Windows 11) · Python phase

Everything below was run, not reasoned about. Where something was **not** run it says so.

## 1. Gate 1 — bit-exact against the reference receiver

`util/gate_bitexact.py CAPTURE --oracle ORACLE.dg`. One off-air capture goes through the
reference receiver (unmodified) and through the GNU Radio chain; the two datagram files must be
the same bytes. Captures and oracles are not in this repository and never will be.

| Decode path | Air | Frames | FEC blocks BCH-clean | Datagrams | Result |
|---|---|---|---|---|---|
| hybrid time interleaver (fast frame decoder) | 11.9 s | 48 / 48 | 3552 / 3552 | 4150 / 4150 | **sha256 identical** |
| convolutional interleaver, LDM pipeline, 4 worker processes | 24.7 s | 102 / 102 | 3534 / 3534 | 12285 / 12285 | **sha256 identical** |
| same, no worker processes | 24.7 s | 102 / 102 | 3534 / 3534 | 12285 / 12285 | **sha256 identical** |

Speed, start-up included: hybrid path 1.16x real time (frame decoder alone about 1.8x);
CTI/LDM path 0.64x end to end, about 0.8x sustained in the decoder. The reference receiver
manages 1.8x on the same capture; the difference is this phase's single Python process, where
the capture reader, front end, link layer and sinks share one interpreter lock.
**The CTI/LDM path is not yet fast enough for live air on this machine. It is not claimed to be.**

## 2. Live air

`apps/atsc3_rx.py --soapy driver=sdrplay --freq <Hz> --antenna <port> --gain RFGR=.. IFGR=.. --udp`
on an SDRplay RSPdx and an indoor antenna; a hybrid-interleaver multiplex; gains from a gr-rxtune
verdict the night before. The radio was shared under a site lock (RXTUNE_LOCK) for every run.

| Run | Result |
|---|---|
| 1 (first attempt) | 3 clean frames, then nothing. Acquisition ran inside `work()`, the radio overflowed, timing dead-reckoned across the hole. |
| 2 (front end on its own thread + decoder feedback) | 1 re-acquisition at start-up, then every frame clean; 92.9 % over 60 s. Driver overflow (`OsO`) during start-up. |
| 3 (+ 1 s of buffer after the source) | no overflow; still 1 re-acquisition: SNR sagged 22 -> 12 dB over the first 8 frames. |
| 4 (+ discard the radio's first 2 s) | **45 s: 164 frames, 12136 / 12136 BCH-clean (100.00 %), 0 re-acquisitions, 0 continuity breaks, 14374 datagrams.** |
| GRC flowgraph `rx_radio_qt.grc` | 50 s: 13690 / 13690 (100.00 %). 75 s: 21234 / 21238 (99.98 %). 75 s: 21161 / 21164 (99.99 %). |
| `examples/tv_bridge.py` (chain -> reference transport -> live directory -> mpv) | 100 s: 28712 / 28712 (100.00 %), 0 re-acquisitions; 185 media segments: 720p60 HEVC video lane, 2 AC-4 audio lanes, 1 subtitle lane; mpv played 1 min 36 s of video to end of file. |
| `tv_bridge.py --tv --sap` (adds the reference AC-4 decoder x2 and its full A/V viewer) | **300 s: picture and sound.** 87639 / 88652 FEC blocks (98.86 %). Output measured, not assumed: the viewer's muxed stream is 270 s of HEVC 720p + English and Spanish stereo + DVB subtitles; mean level -17.8 / -15.9 dB; 0 video decode errors; AC-4 8616 frames, 0 bad. One reception event at ~107 s (14 weak frames, 1 re-acquisition, recovered unaided) is audible as a 5.4 s silence; the only other silence is the 3.4 s lead-in. SNR was 16-18 dB that hour against a ~15 dB cliff (21-22 dB in the morning runs), so a fade is the likely cause - but SNR was not being logged during that run, so that is an inference. It is logged now. |
| same, 240 s, before the launch fix | the decoder, muxer and player all started in the same second and cost a re-acquisition at that moment (4.4 s of silence). Helpers now start one at a time at below-normal priority; the 300 s run was clean through launch. |

`docs/img/rx_radio_qt_live.png` is the third GRC run's window. Its plan readout is switched off
(`Show plan = No`): what a real station signals is that station's data, not this project's.

**Not run:** the CTI/LDM path on live air; any radio other than the RSPdx. The longest run is the 42-minute stress test below.

## 2b. Same-samples stress test against the reference receiver

`util/stress_ab.py` + `util/stress_ab_report.py`. One radio cannot feed two receivers, and two runs an hour
apart are two different channels, so GNU Radio owns the radio, quantises ONCE to 16-bit I/Q, and feeds the
identical sample values to this chain (live) and, through a growing file, to the reference receiver. Each
dropout is then the air (both lost it) or the code (one did).

The run was set for 60 minutes and ended at **42 min when the PC bugchecked** (an unrelated, recurring fault of
that machine). The shared sample file survived, so the reference was run over all 42 minutes of it offline:
the comparison below is still sample for sample. (In the live part the reference also stopped early, at 16 min:
12 s of head start let it reach the end of the growing file after a re-acquisition. Now 90 s.)

| 42 min, identical samples | this GNU Radio chain (live, CPU) | reference receiver (offline, its default GPU path) |
|---|---|---|
| frames decoded | 10193 | 10290 |
| FEC blocks BCH-clean | 749452 / 754282 (**99.360 %**) | 759043 / 761460 (**99.683 %**) |
| re-acquisitions | 4 | 4 |
| sample-continuity breaks | 0 | 0 |
| whole frames shed, decoder behind | 17 (all in the first seconds) | - |
| IP datagrams | 880794 | 882920 |
| datagrams byte-identical in both | 878510 = 99.50 % of the reference's, 99.74 % of ours | |

Loss events over 40 blocks (11 one-minute bins where each side shed a handful of blocks are omitted):

| minute | ours lost | reference lost | verdict |
|---|---|---|---|
| 1 | 1760 | 0 | **ours only** - see below |
| 5 | 1554 | 1184 | the air or the radio: the signal vanished for both (SNR -85 dB) |
| 16 | 1480 | 814 | the same again (SNR -72 dB) |

After minute 17 this chain ran 25 minutes with single-digit block losses. On the two shared dropouts it lost
more than the reference (about 5-9 frames longer to be decoding again).

**The "ours only" event was run to ground, and it is not in this project.** It reproduces offline from the same
samples; the reference receiver's own CPU mode fails at the same place and far worse (79.5 % and 14
re-acquisitions over 150 s, against this chain's 96.4 % and 1); its exact float64 CPU path and its GPU path are
clean. A fresh decoder instance fails identically, so it is not state; the front end is innocent. Switching the
reference's margin levers one at a time pins it to a single one: **smoothing the channel estimate across
carriers**. With smoothing, 0 of 74 blocks converge on frames whose known dummy cells are perfect at 17 dB;
without it, 74 of 74 - the signature of a long echo, which smoothing averages away. The Frame Decoder now
retries a frame that fails at a healthy SNR once with smoothing off and keeps the better result: on that
150 s stretch **96.36 % -> 99.98 %, 108 of 108 frames rescued, 0 re-acquisitions**; nothing is retried in a real
fade (low SNR), and both bit-exact gates still pass. A four-frame reproduction went to the reference receiver's
author. This supersedes the guess in section 2 that the first 5.4 s audio dropout was a fade: it was probably this.

## 3. Datagrams really leave the process

`build/grc/rx_capture.py` (generated by grcc from `examples/rx_capture.grc`, unedited) replaying
the hybrid capture, with an independent Python process joined to the service's multicast group on
the loopback interface: **3827 datagrams, 4.3 MB received.** The flowgraph sent 3889 to that group;
a file replays faster than real time and UDP does not wait, so a listener can miss some. Not a defect,
but do not use the UDP path as an acceptance test - that is what the datagram file sink is for.

## 4. QA (no receiver, no capture, no radio)

`python/atsc3rx/qa_*.py`, 22 tests, all pass. They use `qa_common.fake_receiver()`, a stand-in for
the reference receiver, so that what these blocks ADD is tested on its own:

| File | Tests | What is proven |
|---|---|---|
| `qa_core` | 4 | a frame window is handed over by reference and only once; a radio drops whole frames when the decoder is behind and the backlog never grows; a file waits instead; a missing receiver is a clear error |
| `qa_frame_sync` | 7 | every sample reaches the front end, in order; tracker retuned to the signalled frame length; inline PDUs; 8 weak frames in a row re-acquire and one good frame forgives; stale verdicts are ignored; re-acquisition bumps the generation the decoder watches; a full queue on a radio is counted as a continuity break; settle discard; no L1 on a radio means keep listening |
| `qa_frame_decoder` | 3 | baseband bytes with ABSOLUTE packet bounds; dial, quality, cumulative liveness; weak-frame feedback; inline frames; an unclaimed reference is counted, not crashed on |
| `qa_sinks` | 4 | the dump format byte for byte and its hash; UDP delivery keeping the port; ALP decap emits IPv4 only and `flush()` releases the tail; Capture Source reads cs16 and goes quiet without ending or padding |
| `qa_status_panel` | 3 | lazy Qt import returns the class every time; totals and painting; `Show plan = No` withholds everything a station signals |

grcc compiles both example flowgraphs. **No CI yet** (no Linux build has been done).

## 5. Defects found by testing, all fixed

1. A metadata field of the reference decoder is a dict; treating it as a number killed the decoder thread after frame 1.
2. A frame window costs ~0.46 s to serialise into a PMT vector and back - two frame periods. Windows now travel by reference inside the process (`inline=True` keeps the old behaviour).
3. With serialisation gone the front end outran the decoder and the unclaimed-window cap silently DROPPED 49 of 102 frames - holes in a convolutional interleaver, 1841/1928 FEC. A file now waits; only a radio drops, and it counts.
4. 32 BLAS threads under a loop of small matrix products made the LDM decoder ~4x slower. One BLAS thread, set by the bridge itself (environment for worker processes, threadpoolctl for this one).
5. `flush()` raced: the decoder's flushed output was published asynchronously and the link layer was flushed before it arrived - the last 55 datagrams of a capture were lost. Stages are now drained in order, each only after it has received everything the stage before it sent.
6. Acquisition inside `work()` stalled the flowgraph and overflowed the radio (live run 1).
7. No supervisor: nothing told the front end its timing was wrong. The decoder now reports weak frames back; 8 in a row re-acquire (the reference receiver's rule).
8. A tuner is still settling when its stream opens; locking to those samples cost a re-acquisition every start (live run 3).
9. Capture Source padded silence after end of file, which made the frame count differ from the reference by one. It now goes quiet.
10. `flush()` waited for ten minutes on a front-end thread that GNU Radio had already stopped. It now drains the queue itself in that case.
11. GRC enum parameter given an expression fell back to its default, so `Show plan = No` did nothing in the generated flowgraph. Now a bool. Caught by looking at the screenshot.
13. The reference player reads the lane list once, at launch, and live.json lists audio lanes before video: the bridge started it too early twice. It now waits for a video lane that exists on disk.
14. Starting the viewer stack (3 Python tools, ffmpeg, a player) in one second starved the radio thread: a re-acquisition. Staggered, below-normal priority.
15. The start-up "fix" of prewarming the decoder on the plan pushed the front end past its queue, and exposed a loop: a continuity break forces a re-acquisition, which is slow, which overflows the queue again - four breaks a second, forever (369 in 90 s while the reference decoded everything). A break now discards the whole stale queue, and the queue is deeper. Found by the stress harness in its first 90 seconds.
16. 8-10 whole frames were shed at every start while the decoder built its tables. It now builds them when the plan arrives: 0 shed.
17. `tv_bridge` terminated the viewer before the viewer could close its own player, leaving a paused player on a dead stream (somebody pressed play on it). The bridge now lets the viewer finish, and closes the recorded player if it had to terminate.
12. (test harness) A Python block that goes out of scope while its flowgraph runs is an access violation: `connect()` holds only the C++ half. And a finite stock source ends the flowgraph under message blocks that still have frames in flight - the reason Capture Source exists.

## 6. Not done

- **Gate 2**, TX -> RX loopback against drmpeg/gr-atsc3's V&V flowgraphs: needs a C++ build (Linux).
  Until it passes, this receiver is proven only on the multiplexes two local stations transmit.
- Modes no local station uses: 16K/32K FFT, MISO, channel bonding, multi-subframe frames with
  differing FFT sizes, PLPs other than the core. The reference receiver's own limits apply unchanged.
- No C++ stage exists. Everything DSP is still the reference receiver's code, called in its own order.
