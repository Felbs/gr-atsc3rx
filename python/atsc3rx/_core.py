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
        # One BLAS thread. The receiver fans out with its own threads and processes; a BLAS
        # pool underneath a loop of small matrix products is a throttle, not parallelism
        # (measured here: 32 MKL threads made the LDM decoder 4x slower). The environment
        # covers worker processes; threadpoolctl covers this one, where NumPy is already up.
        for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            os.environ.setdefault(v, "1")
        try:
            import threadpoolctl
            _cache["_blas_limit"] = threadpoolctl.threadpool_limits(limits=1)
        except Exception:                       # not installed: the environment variables still help children
            pass
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


# ---- frame windows by reference ------------------------------------------------------
# A frame window is ~1.7 M complex128 samples. Serialising that into a PMT vector and back
# costs ~0.4 s in Python - more than the frame lasts. Inside one process the window can stay
# where it is: the PDU carries a key, the consumer collects the array. `inline=True` on Frame
# Sync puts the samples in the PDU instead (another process, a file, a debugger). This is a
# Python-phase device; in C++ the hand-off is a tagged stream and costs nothing.
_windows = {}
_win_cv = threading.Condition()
_win_next = [1]
WINDOW_CAP = 8                       # ~27 MB each


def park_window(w, drop=False, timeout=120.0):
    """Park a frame window; returns its key, or None if it was dropped.

    Behind a slow decoder there are two honest things to do. A FILE waits (drop=False):
    every frame matters and nothing is lost by waiting. A RADIO cannot wait - the samples
    keep coming - so it drops this WHOLE frame (drop=True) rather than let the backlog grow
    or lose samples mid-frame. Holes cost a convolutional interleaver dearly, so the caller
    counts them."""
    with _win_cv:
        if len(_windows) >= WINDOW_CAP:
            if drop:
                return None
            _win_cv.wait_for(lambda: len(_windows) < WINDOW_CAP, timeout)
        key = _win_next[0]
        _win_next[0] += 1
        _windows[key] = w
        return key


def claim_window(key):
    with _win_cv:
        w = _windows.pop(key, None)
        _win_cv.notify_all()
        return w
