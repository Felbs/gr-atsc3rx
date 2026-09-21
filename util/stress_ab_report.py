#!/usr/bin/env python
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reads a stress_ab.py output directory and says, for the same samples, who lost what.

  stress_ab_report.py DIR [--md REPORT.md]

  * totals for each receiver (frames, FEC blocks, re-acquisitions, datagrams);
  * the datagram streams compared as multisets: common, only ours, only the reference's;
  * a per-interval loss timeline for both, so each dropout is 'the air' (both) or 'the code' (one).
Datagrams carry broadcast content: this prints counts only."""
import argparse
import collections
import hashlib
import json
import os
import re
import struct
import sys

REC = struct.Struct("<4s4sHHI")
REF_LINE = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\]\s+([\d.]+)s\s+(\d+) Frames.*?FEC (\d+)/(\d+)")


def dg_hashes(path):
    """Counter of datagram digests, streamed (the files run to gigabytes)."""
    c = collections.Counter()
    n = 0
    with open(path, "rb") as fh:
        while True:
            head = fh.read(REC.size)
            if len(head) < REC.size or not any(head):      # a crash leaves a zero-filled tail: not datagrams
                break
            ln = REC.unpack(head)[4]
            body = fh.read(ln)
            if len(body) < ln:
                break
            c[hashlib.blake2b(head + body, digest_size=12).digest()] += 1
            n += 1
    return c, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--md")
    ap.add_argument("--bin", type=float, default=60.0, help="timeline bin, seconds")
    ap.add_argument("--ref", default="ref", help="file prefix of the reference run (ref, or ref_full for an offline re-run)")
    a = ap.parse_args()
    d = a.dir
    out = []

    def say(s=""):
        out.append(s)
        print(s)

    done = json.load(open(os.path.join(d, "done.json")))
    ours = [json.loads(x) for x in open(os.path.join(d, "ours.jsonl")) if x.strip()]
    ref = json.load(open(os.path.join(d, a.ref + ".json"))) if os.path.exists(os.path.join(d, a.ref + ".json")) else {}
    o = done["ours"]
    say(f"run: {done['wall_s']:.0f} s wall ({done['why']}); {done['iq_bytes'] / 1e9:.1f} GB of shared 16-bit I/Q")
    say()
    say("| | this GNU Radio chain | reference receiver |")
    say("|---|---|---|")
    rf, rt, rb = ref.get("frames", 0), ref.get("fec_total", 0), ref.get("bch_zero", 0)
    say(f"| frames decoded | {o['frames']} | {rf} |")
    say(f"| FEC blocks BCH-clean | {o['bch']} / {o['fec']} ({100.0 * o['bch'] / max(o['fec'], 1):.3f} %) | "
        f"{rb} / {rt} ({100.0 * rb / max(rt, 1):.3f} %) |")
    fe = ref.get("front_end", {})
    say(f"| re-acquisitions | {o['reacquisitions']} | {fe.get('reacquire', 0)} |")
    say(f"| sample-continuity breaks / overflow reads | {o['continuity_breaks']} | "
        f"{ref.get('source', {}).get('overflow_reads', 0)} |")
    say(f"| whole frames shed (decoder behind) | {o['frames_dropped']} | see its log |")

    # datagrams
    po, pr = os.path.join(d, "ours.dg"), os.path.join(d, a.ref + ".dg")
    if os.path.exists(po) and os.path.exists(pr):
        co, no = dg_hashes(po)
        cr, nr = dg_hashes(pr)
        common = sum((co & cr).values())
        say(f"| IP datagrams | {no} | {nr} |")
        say()
        say(f"datagrams in common (byte-identical): **{common}** = {100.0 * common / max(nr, 1):.3f} % of the reference's, "
            f"{100.0 * common / max(no, 1):.3f} % of ours")
        say(f"only in ours: {no - common}    only in the reference: {nr - common}")
        say("(the reference reads the shared file from its first sample to its last, after the radio has closed; "
            "this chain runs live. So the reference-only datagrams are this chain's start-up, its dropouts, "
            "and the last second or two in flight when the flowgraph stopped)")

    # timeline
    snr = [x["snr"] for x in ours if isinstance(x.get("snr"), (int, float)) and x["snr"] > -5]
    if snr:
        s = sorted(snr)
        say()
        say(f"SNR off the dummy cells (ours): median {s[len(s) // 2]:.1f} dB, 5th percentile {s[len(s) // 20]:.1f}, "
            f"min {s[0]:.1f}, max {s[-1]:.1f}")
    frame_sec = (ref.get('air_s', 0) / ref['frames']) if ref.get('frames') else 0.2471
    refpts = []
    path = os.path.join(d, a.ref + ".log")
    if os.path.exists(path):
        for line in open(path, errors="replace"):
            m = REF_LINE.match(line)
            if m:
                refpts.append((int(m.group(5)) * frame_sec, int(m.group(6)), int(m.group(7))))
    say()
    say(f"loss timeline, {a.bin:.0f} s bins (FEC blocks that did NOT come out clean); quiet bins omitted")
    say()
    say("| t (min) | ours lost | ours re-acq | ours min SNR | reference lost | verdict |")
    say("|---|---|---|---|---|---|")

    def binned(points, key_t, lost_of):
        b = collections.OrderedDict()
        prev = None
        for p in points:
            if prev is not None:
                k = int(key_t(p) // a.bin)
                b[k] = b.get(k, 0) + max(0, lost_of(p) - lost_of(prev))
            prev = p
        return b
    bo = binned(ours, lambda x: x["t"], lambda x: x["fec"] - x["bch"])
    # the reference's points are already on the AIR clock (frames x frame period), like ours
    br = binned(refpts, lambda x: x[0], lambda x: x[2] - x[1])
    bra, prev = {}, None
    for x in ours:
        if prev is not None:
            k = int(x["t"] // a.bin)
            bra[k] = bra.get(k, 0) + (x["reacq"] - prev["reacq"])
        prev = x
    bsn = {}
    for x in ours:
        v = x.get("snr_min")
        if isinstance(v, (int, float)):
            k = int(x["t"] // a.bin)
            bsn[k] = min(bsn.get(k, 99.0), v)
    both = ours_only = ref_only = small = 0
    SMALL = 40                       # under half a frame of blocks: both receivers shed a few at the cliff all hour
    for k in sorted(set(bo) | set(br)):
        lo = bo.get(k, 0)
        lr = br.get(k, 0)
        lr_near = sum(br.get(j, 0) for j in (k - 1, k, k + 1))     # the reference's clock is frames, ours is wall
        lo_near = sum(bo.get(j, 0) for j in (k - 1, k, k + 1))
        if lo == 0 and lr == 0:
            continue
        if max(lo, lr) < SMALL:
            small += 1
            continue
        if lo >= SMALL and lr_near >= 0.25 * lo:
            v = "the air (both)"
            both += 1
        elif lo >= SMALL:
            v = "**ours only**"
            ours_only += 1
        elif lo_near >= 0.25 * lr:
            continue                 # the neighbouring bin already reported it
        else:
            v = "reference only"
            ref_only += 1
        sn = bsn.get(k)
        say(f"| {k * a.bin / 60:.0f} | {lo} | {bra.get(k, 0)} | {'' if sn is None else f'{sn:.1f} dB'} | {lr} | {v} |")
    say()
    say(f"({small} more bins where neither side lost as many as {SMALL} blocks are omitted)")
    say()
    say(f"events: {both} shared with the reference (the air), {ours_only} ours only, {ref_only} reference only")
    if a.md:
        with open(a.md, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
