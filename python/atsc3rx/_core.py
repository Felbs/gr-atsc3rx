#
# Copyright 2026 gr-atsc3rx authors.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""PYTHON-PHASE BRIDGE to the reference receiver.

During the Python phases these blocks do not re-implement ATSC 3.0: they call the
reference receiver's own code, stage by stage, so that each GNU Radio block can be
held BIT-EXACT against it before any stage is rewritten in C++. The reference
receiver is a separate project and is not part of this repository; tell us where it
is with `receiver_dir=` or the environment variable ATSC3_RECEIVER_DIR (the directory
that contains `lab/`)."""
import importlib
import os
import sys
import threading

_lock = threading.Lock()
_cache = {}


class ReceiverNotFound(ImportError):
    pass


def load(receiver_dir=None, quiet=True):
    """Import the reference receiver's modules once per process."""
    root = receiver_dir or os.environ.get("ATSC3_RECEIVER_DIR")
    if not root:
        raise ReceiverNotFound("set receiver_dir= or ATSC3_RECEIVER_DIR to the reference receiver")
    lab = os.path.join(os.path.abspath(root), "lab")
    if not os.path.isdir(lab):
        raise ReceiverNotFound(f"{lab} does not exist")
    with _lock:
        if lab in _cache:
            return _cache[lab]
        # importing the receiver's fast path would otherwise import torch into the
        # flowgraph process; the GPU stays optional and is never loaded by default
        os.environ.setdefault("M9_NO_TORCH", "1")
        if lab not in sys.path:
            sys.path.insert(0, lab)

        class NS:
            pass
        ns = NS()
        ns.ST = importlib.import_module("m11_stream")
        ns.M44 = importlib.import_module("m44_ldm")
        ns.m9_fast = importlib.import_module("m9_fast")
        ns.P6 = importlib.import_module("m6_payload")
        ns.R7 = importlib.import_module("m7_route")
        ns.M16 = importlib.import_module("m16_margin")
        ns.lines = []
        if quiet:
            ns.ST.log = lambda *a, **k: ns.lines.append(" ".join(str(x) for x in a))
        _cache[lab] = ns
        return ns
