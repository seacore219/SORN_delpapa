#!/usr/bin/env python3
"""Locate where a SORN run's oscillation comes from.

Three questions, answered from the h5 files alone -- no re-simulation:

  1. Are the parameters actually identical between an old (good) run and a
     new (bad) one?  The full config is stored in the h5, so this is a
     straight diff rather than a recollection of what was set.
  2. Does the oscillation exist in `activity` (recorded by untouched original
     code) as well as in `Spikes` (recorded by the stats added later)?  If it
     is only in Spikes, the recording is at fault; if it is in both, the
     dynamics really are oscillating.
  3. When during the run does it start?  The old protocol only ever ran
     plastic dynamics to step 4,000,000; anything after that is territory no
     previous batch reached.

Usage:
    python3 diagnose.py new_run/common/result.h5
    python3 diagnose.py new_run/common/result.h5 --compare old_run/common/result.h5
"""

import argparse
import sys

import h5py
import numpy as np
from scipy import signal


def walk_config(group, prefix=""):
    """Flatten the h5 'c' group into {path: value}."""
    out = {}
    for key in group:
        item = group[key]
        path = "%s/%s" % (prefix, key) if prefix else key
        if isinstance(item, h5py.Group):
            out.update(walk_config(item, path))
        else:
            value = item[()]
            value = np.asarray(value).ravel()
            out[path] = value[0] if value.size == 1 else "array%s" % (value.shape,)
    return out


def compare_configs(path_a, path_b):
    with h5py.File(path_a, "r") as fa, h5py.File(path_b, "r") as fb:
        ca, cb = walk_config(fa["c"]), walk_config(fb["c"])
    keys = sorted(set(ca) | set(cb))
    differences = []
    for key in keys:
        va, vb = ca.get(key, "<missing>"), cb.get(key, "<missing>")
        same = (isinstance(va, type(vb)) and np.all(va == vb)) or str(va) == str(vb)
        if not same:
            differences.append((key, va, vb))
    print("=== config diff (new vs old) ===")
    if not differences:
        print("  identical -- every parameter matches")
    for key, va, vb in differences:
        print("  %-34s new=%-20s old=%s" % (key, va, vb))
    print()


def peakiness(x, nperseg=4096):
    """Ratio of the strongest spectral peak to the median power.

    A broad 1/f-like spectrum sits near 1-10; a sharp oscillation with
    harmonics pushes this far higher.
    """
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    if x.std() == 0:
        return 0.0, 0.0
    freqs, power = signal.welch(x, nperseg=min(nperseg, len(x)))
    body = power[1:]                      # drop DC
    freqs = freqs[1:]
    if body.size == 0 or np.median(body) == 0:
        return 0.0, 0.0
    index = int(np.argmax(body))
    return body[index] / np.median(body), freqs[index]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("h5path")
    parser.add_argument("--compare", metavar="OLD_H5",
                        help="an older run to diff the config against")
    parser.add_argument("--windows", type=int, default=12,
                        help="how many slices to split the run into (default 12)")
    args = parser.parse_args()

    if args.compare:
        compare_configs(args.h5path, args.compare)

    with h5py.File(args.h5path, "r") as f:
        N_e = int(f["c/N_e"][0])
        N_steps = int(f["c/N_steps"][0])
        activity = f["activity"][0][:]
        has_spikes = "Spikes" in f
        print("=== run ===")
        print("  N_e=%d  N_steps=%d  h_ip=%.4f  Spikes stored=%s"
              % (N_e, N_steps, float(f["c/h_ip"][0]), has_spikes))
        print()

        # -- 2. is the oscillation in BOTH recordings? ----------------------
        counts_activity = np.round(activity * N_e)
        if has_spikes:
            spikes = f["Spikes"]            # stored as (1, N_e, T_saved)
            T_saved = spikes.shape[2]
            offset = N_steps - T_saved
            # compare over a chunk both recordings cover, past the transient
            hi = N_steps
            lo = max(offset, hi - 200_000)
            counts_spikes = spikes[0, :, lo - offset:hi - offset].sum(axis=0)
            seg_activity = counts_activity[lo:hi]
            agree = np.array_equal(counts_spikes, seg_activity)
            print("=== recording cross-check, steps %d-%d ===" % (lo, hi))
            print("  Spikes.sum(0) == round(activity*N_e): %s" % agree)
            if not agree:
                delta = np.abs(counts_spikes - seg_activity)
                print("  MISMATCH -- max |diff|=%g, mismatching steps=%d/%d"
                      % (delta.max(), int((delta > 0).sum()), delta.size))
                print("  -> the Spikes array does not match the activity trace;")
                print("     the added spike recording is suspect, not the dynamics")
            pk_s, fr_s = peakiness(counts_spikes)
            pk_a, fr_a = peakiness(seg_activity)
            print("  peakiness  Spikes=%.1f (f=%.4f/step)   activity=%.1f (f=%.4f/step)"
                  % (pk_s, fr_s, pk_a, fr_a))
            if pk_a > 20:
                print("  -> present in `activity` too, which untouched original code")
                print("     records: the network really is oscillating")
            print()

        # -- 3. when does it start? ----------------------------------------
        print("=== oscillation vs time (from `activity`) ===")
        print("  %-22s %8s %10s %12s" % ("window", "mean act", "peakiness", "peak freq"))
        edges = np.linspace(0, len(counts_activity), args.windows + 1, dtype=int)
        for i in range(args.windows):
            lo, hi = edges[i], edges[i + 1]
            seg = counts_activity[lo:hi]
            pk, fr = peakiness(seg)
            flag = "   <-- oscillating" if pk > 20 else ""
            print("  %9d-%-11d %8.2f %10.1f %12.5f%s"
                  % (lo, hi, seg.mean(), pk, fr, flag))
        print()
        print("  The original frozen protocol only ever ran plastic dynamics to")
        print("  step 4,000,000. If peakiness climbs past that point, the extra")
        print("  2M plastic steps are what drove the network out of the regime.")

        if "ConnectionFraction" in f:
            cf = f["ConnectionFraction"][0][:]
            print()
            print("=== connection fraction (is the network still drifting?) ===")
            for i in range(args.windows):
                lo, hi = edges[i], edges[i + 1]
                print("  %9d-%-11d %.6f" % (lo, hi, cf[lo:hi].mean()))


if __name__ == "__main__":
    sys.exit(main())
