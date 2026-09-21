# gr-atsc3rx — design (DRAFT for approval; no code written)

Status 2026-09-21: name check and prior-art read done (`PRIOR_ART.md`). This is the gate.
Working name `gr-atsc3rx`. No repository created, nothing pushed.

## 1. What it is, and what it is not

An ATSC 3.0 (A/321 + A/322) **physical-layer receiver as GNU Radio 3.10 blocks**: complex
baseband in, IP datagrams out. It is a PORT of a receiver we already have working on live air -
no algorithm here is new; the work is streaming structure and speed.

- **In scope:** bootstrap -> frame sync -> OFDM demod -> channel estimation -> L1 signalling ->
  de-interleaving -> soft demapping -> LDM -> LDPC/BCH -> baseband packets -> ALP -> IP/UDP PDUs.
- **Out of scope, on purpose:** ROUTE / MMTP, the service guide, AC-4, the player. They are not
  signal processing; the existing receiver keeps them and consumes our UDP output.
- **Never:** decryption or any circumvention of A3SA-protected services.
- It is its **own repository**, not part of gr-rxtune. rxtune's only stake is one recipe and one
  dial adapter (this receiver will publish per-frame SNR and BCH-clean counts as messages, so
  rxtune can tune it live in one flowgraph with no capture-per-cell detour).

## 2. The one architectural decision: geometry arrives over the air

FFT size, guard interval, pilot pattern, number of symbols, PLP layout, ModCod, interleaver
mode - all of it is signalled in L1, per frame and per SUBFRAME. GNU Radio blocks want item
sizes fixed at construction. The in-tree DVB-T receiver fixes everything and cannot be the model.

**Decision: a tagged sample stream up to the FFT, then per-frame PDUs.**

```
 samples ──► bootstrap_sync ──► [tag: frame_start, bootstrap{}] ──► l1_decoder ──► [tag: plan{}]
                                                                        │ (also a message: plan)
 samples+tags ──► frame_demod ──► PDU per subframe: {cells: c32vector, csi: f32vector, meta: plan slice}
 PDU ──► deinterleave ──► PDU per FEC-block batch ──► demap(LLR, CSI-weighted) ──► ldpc_bch ──► bbp ──► alp ──► UDP PDUs
```

- Up to and including the FFT the data is a **stream with tags** (the gr-dvbs2rx `plsync`
  pattern): the first sample of each frame carries a dictionary with everything L1 said.
- After the FFT the natural unit is a subframe of cells, whose size changes - so it travels as a
  **PDU with metadata** (the 802.11 pattern). CSI rides with the cells; an equaliser that divides
  by H must hand |H| forward (our own measured law: best frames went 7/17 -> 17/17 blocks).
- The **plan** (our existing `LdmPlan` / geometry object, serialised to a PMT dict) is the
  contract between blocks. It is also published on a message port for anyone who wants to look.
- Cost accepted: PDUs copy. At ~130k cells per frame and ~4 frames/s that is small next to LDPC.

## 3. Blocks

| # | Block | In -> out | Lang (final) | Ported from |
|---|---|---|---|---|
| 1 | `bootstrap_sync` | samples -> samples + `frame_start` tag {version, bandwidth, preamble structure, time offset, CFO} | C++ | bootstrap detector |
| 2 | `l1_decoder` | tagged samples -> same + `plan` tag; message out `plan` | C++ (Python first) | L1-Basic / L1-Detail, plan builder |
| 3 | `frame_demod` | tagged samples -> PDU/subframe {cells, csi} | C++ | front end: per-carrier frame-length tracker, fine timing, CP removal, FFT (8K/16K/32K per subframe), pilots, channel estimate, CPE |
| 4 | `freq_deinterleaver` | PDU -> PDU | C++ | incl. the SIGNALLED subframe-boundary null count (the 190-cell lesson) |
| 5 | `time_deinterleaver` | PDU -> PDU | C++ | CTI and HTI |
| 6 | `cell_deinterleaver` + PLP demux | PDU -> PDU per PLP | C++ | A/322 7.1.5.2, gated on the spec's printed vector |
| 7 | `ldm_canceller` | PDU -> PDU (core), PDU (enhanced) | C++ | decode core, re-modulate, subtract |
| 8 | `nuc_demapper` | cells+csi -> int8 LLRs | C++ / SIMD | non-uniform QPSK..4096QAM, `min(|H|^2/mean, 1)` weighting |
| 9 | `ldpc_bch_decoder` | LLR batch -> BB frames + {converged, bch_clean, iters} | C++ | bit de-interleaver, LDPC (engine: see 5), BCH/CRC, descrambler |
| 10 | `bbp_alp_decap` | BB frames -> IP datagrams as PDUs | C++ (Python first) | baseband packets, ALP |
| 11 | `quality_probe` | messages: per-frame dummy-cell SNR, BCH-clean share, lock state | Python | the dial + liveness for rxtune, a Qt number sink, a log |

Stock blocks around them: Soapy/UHD/File source, rational resampler to 6.912 MS/s, Socket PDU /
UDP sink, the Qt sinks. One hier block `atsc3_rx` wires 1-10 for people who just want datagrams.

## 4. Acceptance: three gates, none of which needs a broadcaster

1. **Bit-exact against the existing receiver.** Same banked capture through both; every
   intermediate is compared at a stage boundary (cells after the frequency de-interleaver, LLR
   signs, BB frames, datagrams). A stage is not "ported" until its diff count is zero. This is the
   existing project's own gate, reused.
2. **TX -> RX loopback against drmpeg's transmitter.** Its V&V flowgraphs at 6.912 MS/s into a File
   Sink, then into us: a radio-free test for every mode the transmitter can make, including ones no
   local station radiates (4096QAM, MISO, FDM). Transport stream in == transport stream out. This
   is the CI test, and it ships no captured broadcast content.
3. **Live air**, on the carriers we already decode, watched through the existing player.

Rule carried over: *a strong bootstrap and a clean L1 CRC say nothing about the data cells* -
each gate is on decoded payload, never on "it locked".

## 5. LDPC engine

Start with **our own C kernel** behind block 9 (it is the known quantity and already real-time).
In parallel, evaluate the **xdsopl SIMD batch decoder** as ported in gr-dvbs2rx: Type B tables
should fit its DVB-shaped structs; Type A needs an extended path. Whichever wins gate 1 and is
faster on a plain CPU ships as default; the GPU path stays optional (house rule: GPU always
optional, CPU fallback always present).

## 6. Order of work

| Phase | Deliverable | Gate | Rough size |
|---|---|---|---|
| 0 | Scaffold (gr_modtool, C++), CI skeleton, loopback bench: drmpeg TX -> cf32 files for 5 V&V modes | files decode with the EXISTING receiver | days |
| 1 | `bootstrap_sync` + `l1_decoder` (Python blocks). **A useful tool on its own: point it at any ATSC 3.0 carrier and it prints what is being transmitted.** | plan == existing receiver's plan on captures + all V&V modes | 1-2 weeks |
| 2 | One Python block wrapping the existing front end -> PDUs; blocks 4-10 as thin Python wrappers over existing kernels | gate 1 end to end, not real time | 2-3 weeks |
| 3 | Split and move to C++ in order of cost: `frame_demod`, demapper, LDPC, de-interleavers | gate 1 per stage; real time on 6 cores | 2-3 months |
| 4 | LDM, multi-subframe, multi-PLP, HTI corner cases, MISO | gate 2 modes | ongoing |

Development happens on **Linux** (the Ubuntu rig): C++ OOT modules build far more easily there,
and gr-rxtune's first lesson from this PC was a stale `GR_PREFIX` pointing tools at the wrong GNU
Radio. Windows/radioconda support comes from conda packaging later, not from hand builds.

## 7. Decisions needed from the owner

1. **Name:** `gr-atsc3rx` (recommended; mirrors `gr-dvbs2rx`), or another of the free ones.
2. **Licence:** the existing receiver is Apache-2.0 for its patent grant; GNU Radio OOTs are
   conventionally GPL-3, and only GPL-3 lets us take the gr-dvbs2rx LDPC port and diff-by-copy
   against drmpeg's tables. Options: (a) **GPL-3.0-or-later** for this module, with our own
   Apache-2.0 code flowing in one way [recommended: it is what the ecosystem expects, and the
   patent argument is unchanged because we hold no patents either way]; (b) Apache-2.0 here too,
   using only our own kernels and xdsopl (0BSD), and treating all GPL code as an oracle, as today.
3. **Where the algorithms live:** port by COPYING from the existing receiver into this repo
   (two copies to keep in step), or make the existing receiver a dependency of the Python-first
   phases and only copy when a stage goes to C++ [recommended].
4. **Say hello to Ron Economos (drmpeg)?** His transmitter is our test bench and he is the
   obvious first reader. Not before there is something that decodes his V&V vectors.
5. Same standing rules as gr-rxtune: no repo, no push, no contact with anyone without your word;
   dox gate before every push; no captured broadcast content in the repository, ever.
