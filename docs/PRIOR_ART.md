# Prior art — an ATSC 3.0 physical-layer RECEIVER for GNU Radio

Refreshed 2026-09-21. This extends, and does not replace, the August 2026 census in our
existing ATSC 3.0 receiver project (its `PRIOR_ART.md`, 1,173 lines, still the reference
for the standard-level questions). Everything below was read from the GitHub API, raw
source, or the named page; items not fetched are marked [unverified].

**Summary.** Nobody has published a working open-source ATSC 3.0 PHY receiver other than
ours. GNU Radio has a complete, maintained, V&V-validated **transmitter** (`drmpeg/gr-atsc3`)
and no receive side at all - not for ATSC 3.0, and not even for its closest cousin, DVB-T2.
The reusable pieces are: the gr-dvbs2rx design for run-time frame parameters, the xdsopl LDPC
engine, and the transmitter itself as a radio-free test source.

## Name check

`gr-atsc3rx`, `gr-atsc3-rx`, `gr-nextgentv`, `gr-a3rx`, `gr-ngtv`: no GitHub repository and no
CGRAN entry under any of them. `gr-atsc3` is drmpeg's transmitter (GitHub and CGRAN).
Near-collisions: `awanga/usrp-atsc3` calls its own module "gr-atsc3"; `lukefay/ATSC3_Rx` is an
upper-layer (ROUTE/DASH) receiver; "ngtv" is a crowded namespace of unrelated projects.
**`gr-atsc3rx` follows the established convention** (`gr-dvbs2rx`) and says what it is.

## 1. drmpeg/gr-atsc3 — the transmitter (GPL-3.0-or-later, Ron Economos)

Still transmit-only: 19 blocks, zero receive blocks. 235 commits, last 2026-06-18. CMake wants
GNU Radio >= 3.9, C++17. Validated against the ATSC verification-and-validation suite; examples
are named after those vectors (`vv031`, `vv320`, `vv410`, `vv503`, `vv504`).

- **Implements:** FFT 8K/16K/32K; all 12 guard intervals; all 16 scattered-pilot patterns and 5
  boosts; reduced-carrier modes; LDPC 2/15..13/15 at 16200 and 64800 (separate Type A / Type B
  encoders); QPSK..4096QAM non-uniform constellations; BCH / CRC32 / none; time interleaver off /
  CTI / HTI; frequency interleaver; LDM with injection level 0..-25 dB; multi-PLP TDM and FDM;
  multiple subframes; MISO; tone-reservation PAPR; L1-Basic modes 1-5, L1-Detail modes 1-7.
- **Does not implement (its README):** mixed TDM+FDM, the Convolutional Delay Line, channel
  bonding, L1-Detail segmentation and additional parity, MIMO, ACE PAPR, BB frame counter, TxID,
  IP input (it takes a transport stream).
- **Why it matters to us:** its flowgraphs run at 384000 x 18 = **6.912 MS/s complex float** -
  exactly our receiver's native rate. Replace the USRP sink with a File Sink and it is a
  bit-exact, fully-parameterised **signal generator for a receiver under test**, with no radio, no
  station, and nothing local in the test data. `vv410` alone gives two subframes (8K/GI 1024/SP3_4
  and 32K/GI 768/SP32_2), HTI, two PLPs.
- **Licence:** GPL-3.0-or-later. Usable as a test tool by anyone; usable as a *source* only by a
  GPL-3 project (see "Licensing" below). Our existing project deliberately used it as a comparison
  oracle, not a source.

## 2. Receivers: none that work

- **awanga/usrp-atsc3** - describes itself as "a full ATSC 3.0 physical-layer receiver"; 70 commits,
  a "v1.0.0 MVP" with "652 passing tests", no licence file. Read at source level it cannot decode
  air: its LDPC parity matrices are *generated procedurally* to "approximate" the A/322 tables
  rather than containing them; its L1 decoder uses DVB-T2 terminology and hard decisions with FEC
  marked "post-MVP"; its own task list leaves "end-to-end playback test with a real capture"
  unchecked. Reassessed, same conclusion as August: a well-tooled scaffold.
- Upper-layer only: `lukefay/ATSC3_Rx` (ROUTE/DASH over IP), `libatsc3` (MIT, A/331 and up),
  `nekohkr/danttoUHD` (includes decryption - out of scope, do not use).
- SDRangel: no ATSC plugin. SatDump / SDR++: nothing found [unverified]. GRCon 2024-2026
  programmes: no ATSC, DVB or LDPC talk. Academic: a 2017 GNU Radio *modulator* paper; no ETRI or
  ONE Media open code found.
- The transmitter's author has argued publicly that software LDPC at full ATSC 3.0 rates is not
  feasible on x86. Our receiver plays live television in real time on a six-core CPU. That is the
  prevailing assumption this project would overturn, in public, inside GNU Radio.

## 3. How GNU Radio's relatives are built

| Relative | Pattern | Copy? |
|---|---|---|
| **in-tree gr-dtv DVB-T receiver** | stream -> `ofdm_sym_acquisition` (Van de Beek CP correlation, fractional CFO) -> vectors of `fft_length` -> `demod_reference_signals` (integer CFO, pilots, channel estimate, TPS) -> demap -> decoders. Tags `sync_start`, `superframe_start`, `symbol_index`. **Every dimension fixed at construction.** | The block SPLIT and the symbol-index tags: yes. Fixed vector length: **no** - ATSC 3.0 changes FFT size per subframe. |
| **a DVB-T2 receiver OOT** | does not exist. `drmpeg/gr-dvbs2rx` has only the BICM back end; `krisabo/gr-dvbt2rx` is an empty shell; gr-dvbt2 / gr-dvbt2ll are transmitters. | Nothing to copy; we would be first here too. |
| **igorauad/gr-dvbs2rx** (GPL-3) | `plsync_cc` decodes the frame header AT RUN TIME and **tags the first sample of each frame with a dictionary of its parameters**; filters select which MODCODs pass; a message port steers an external rotator. Downstream FEC is constant-mode only. | **The central pattern.** Parameters that arrive over the air travel as tags on the stream, not as constructor arguments. |
| **gr-ieee802-11** | fixed-size symbol vectors; the SIGNAL field is decoded per frame into a tag dictionary that includes **`csi`**; the MAC layer leaves as PDUs. | CSI handed forward as a tag; PDUs from the framing layer upward. |

## 4. LDPC engines a GPL-3 module may use

- **xdsopl/LDPC** - 0BSD. Min-sum family, sum-product, layered or flooding, **SIMD batch
  decoding** (AVX2 / SSE4.1 / NEON), int8 LLRs. Already ported into gr-dvbs2rx (`lib/ldpc_decoder/`)
  with DVB table structs `{M=360, N, K, DEG[], LEN[], POS[]}`. ATSC 3.0 **Type B** tables have that
  shape and should drop in; **Type A** (rates 2,3,4,5,7/15) has a different parity structure and
  needs an extended path [inferred, not yet verified in source].
- **Our own C / GPU kernel** - already decodes live air in real time; the known quantity.
- aff3ct (MIT) [unverified]; gr-fec's LDPC is not practical at 64800 [unverified].

## 5. Standards, patents, content protection (facts, not advice)

- A/321 and A/322 (2026-06 editions) are free downloads from atsc.org, no login.
- Patent statements for both are on file at ATSC (Samsung, LG, Sony, Panasonic, Sharp, Qualcomm,
  ONE Media and others). Two pools exist - Avanci Broadcast and Via LA - and both license
  *manufacturers of end-user products* per unit. Neither says anything about software, open source
  or SDR, and no public discussion of open-source receivers in that context was found.
- A3SA content protection: not implemented, not attempted, ever. Encrypted services are surfaced as
  encrypted. This project ends at IP datagrams and never touches a content key.

## Licensing (a decision for the owner - see DESIGN.md)

Our existing receiver is **Apache-2.0**, chosen for its explicit patent grant. GNU Radio OOT
modules are conventionally **GPL-3**, and only a GPL-3 module may incorporate the gr-dvbs2rx LDPC
port or compare-by-copy against drmpeg's tables. Apache-2.0 code may flow INTO a GPL-3 project; it
cannot flow back. xdsopl/LDPC itself (0BSD) is usable under either.

## What would be ours

| | drmpeg/gr-atsc3 | awanga/usrp-atsc3 | libatsc3 | gr-atsc3rx |
|---|---|---|---|---|
| Direction | TX | RX (claimed) | above IP | **RX** |
| Decodes real air | n/a | no | n/a | **yes (algorithms proven live)** |
| Real A/322 LDPC tables | yes | no (approximated) | n/a | yes |
| Run-time geometry from L1 | n/a | no | n/a | **tagged stream** |
| CSI-weighted LLRs, per-carrier frame timing, LDM | n/a | no | n/a | yes |
| Radio-free test bench | is one | - | - | **TX -> RX loopback against it** |

## Sources

github.com/drmpeg/gr-atsc3 (README, grc/, lib/, examples/) - github.com/awanga/usrp-atsc3
(lib/fec/ldpc_matrix.cc, lib/framing/l1_decoder.cc, TASKS.md) - github.com/igorauad/gr-dvbs2rx
(lib/plsync_cc_impl, lib/ldpc_decoder/) - github.com/drmpeg/gr-dvbs2rx - gnuradio gr-dtv DVB-T
receiver sources - github.com/bastibl/gr-ieee802-11 (frame_equalizer) - github.com/xdsopl/LDPC -
cgran.org - events.gnuradio.org (GRCon24/25/26) - atsc.org A/321, A/322 and patent statements -
avanci.com/broadcast - our own PRIOR_ART.md (August 2026).
