# gr-atsc3rx

**An ATSC 3.0 (NextGen TV) physical-layer receiver for GNU Radio. Complex baseband in, IP
datagrams out.**

GNU Radio has had a complete ATSC 3.0 *transmitter* for years
([drmpeg/gr-atsc3](https://github.com/drmpeg/gr-atsc3)). It has never had the other half -
not for ATSC 3.0, and not for its cousin DVB-T2 either. This is the other half.

> **Status: Python phase (0.1).** The blocks below work end to end and are held **bit-exact**
> against a reference receiver that decodes live television - but in this phase they *call*
> that receiver's code rather than re-implementing it, so you need it installed, and the
> chain runs at about 0.4x real time. Stages move to C++ one at a time, each only after it
> passes the same gate. See `docs/DESIGN.md` for the plan and `docs/PRIOR_ART.md` for the field.

## What it does

```
 complex samples ─► Frame Sync ─► Frame Decoder ─► ALP Decap ─► UDP / your application
                       │              │
                       └─ plan        └─ dial (SNR) + liveness (BCH-clean blocks)
```

| Block | In -> out |
|---|---|
| **ATSC3 Frame Sync** | samples -> one PDU per frame. Finds the bootstrap, decodes L1-Basic / L1-Detail, builds the frame **plan from what is signalled** (never from the channel number), tracks timing at the signalled frame length. The plan rides in every frame's metadata, because in ATSC 3.0 the geometry arrives over the air - FFT size, guard interval, pilots and PLP layout can change per subframe. |
| **ATSC3 Frame Decoder** | frame -> baseband payload. OFDM demod, channel estimate, de-interleaving, soft demapping, LDPC, BCH. Publishes a **dial** that is continuous below the decode cliff (SNR off the frame's known dummy cells) and **liveness** that cannot be faked (BCH-clean block count) - both plug straight into [gr-rxtune](https://github.com/Felbs/gr-rxtune). |
| **ATSC3 ALP Decap** | baseband payload -> IPv4/UDP datagrams as PDUs. **GNU Radio's job ends here.** ROUTE, MMTP, the guide, audio and video belong to whatever consumes the datagrams. |
| **ATSC3 Capture Source** | a raw IQ file as a stream that goes quiet, rather than ending the flowgraph, at end of file - message blocks still have frames in flight. |
| **ATSC3 Datagram File Sink** | the acceptance-test sink: writes and hashes datagrams in the reference receiver's dump format. |

`apps/atsc3_identify.py` - point it at a capture or a SoapySDR device and in about two seconds
it prints what the carrier is transmitting: FFT size, guard interval, pilot pattern, frame
length, every PLP with its modulation and code rate, time-interleaver mode, LDM.

## It never decrypts

Some ATSC 3.0 services are protected (A3SA). This project does not implement, attempt, link to
or discuss circumventing that, and never will. It stops at IP datagrams and never touches a
content key.

## How it is tested

1. **Bit-exact against the reference receiver** (`util/gate_bitexact.py`): one capture through
   both; the datagram streams must match byte for byte, in order. Current result on a 12 s
   off-air capture: 49 frames, 3626 / 3626 FEC blocks BCH-clean, **all 4150 reference datagrams
   reproduced exactly** (+1 trailing datagram from a final frame the reference run leaves
   unfinished at end of file). A stage is not "ported" until this still passes.
2. **TX -> RX loopback against drmpeg/gr-atsc3** - its V&V flowgraphs run at 6.912 MS/s, our
   native rate, so they are a radio-free signal generator for every mode it can make.
   *Not yet run: it needs a C++ build environment (Linux).*
3. **Live air.**

No captured broadcast content is, or ever will be, in this repository: test signals come from
the transmitter in (2); captures and their oracles stay on the machine that made them.

## Try it (Python phase)

```
export ATSC3_RECEIVER_DIR=/path/to/reference/receiver        # the directory containing lab/
python apps/atsc3_identify.py --capture some.cs16            # or --soapy "driver=..." --freq HZ
python util/gate_bitexact.py some.cs16 --oracle some.dg      # oracle = the reference receiver's --dump-dg
```

Nothing is compiled yet. From the source tree, `examples/_devpath.py` makes
`from gnuradio import atsc3rx` resolve here; for GRC set `GRC_BLOCKS_PATH` to this repo's `grc/`.

## Licence

GPL-3.0-or-later, the GNU Radio convention - which also lets later phases use the SIMD LDPC
engine from gr-dvbs2rx. The reference receiver is a separate project under Apache-2.0; code
flows from it into here, not back. Standards documents A/321 and A/322 are free downloads from
atsc.org. ATSC 3.0 is covered by third-party patents and patent pools that license makers of
end-user products; nothing here grants, or could grant, rights in those.

## Acknowledgments & prior art

- **[gr-atsc3](https://github.com/drmpeg/gr-atsc3)** (Ron Economos) - the transmitter, validated
  against the ATSC verification suite, and therefore the test bench for this receiver and an
  independent reading of A/322 to check ours against.
- **[gr-dvbs2rx](https://github.com/igorauad/gr-dvbs2rx)** (Igor Freire, Ahmet Inan, Ron
  Economos) - the pattern this module is built on: decode the frame's parameters at run time
  and *tag the frame with them*. Also the home of the LDPC engine we expect to use.
- **[xdsopl/LDPC](https://github.com/xdsopl/LDPC)** (Ahmet Inan) - SIMD batch LDPC decoding.
- **[gr-ieee802-11](https://github.com/bastibl/gr-ieee802-11)** (Bastian Bloessl) - CSI carried
  forward with the data, and PDUs from the framing layer up.
- **GNU Radio's in-tree DVB-T receiver** - the block split (acquisition, FFT, pilots/equaliser,
  demap) that every OFDM broadcast receiver inherits.
- **[libatsc3](https://github.com/kansonkong/libatsc3)** - for everything above IP.
