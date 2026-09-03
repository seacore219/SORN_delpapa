"""
Visual diagnostic for avalanche thresholding. Plots:
  1. A raster snippet (so you can eyeball actual spiking activity)
  2. The population-count trace for that snippet, with perc_threshold marked
  3. A histogram of population counts across the FULL analysis window (log y),
     with perc_threshold marked -- this is the distribution get_avalanches
     actually computes perc_threshold from, so you can see the "pileup" near
     zero directly instead of just inferring it from the number that comes out.

Use this to compare two runs side by side (e.g. h_ip=0.07 vs h_ip=0.08, or an
old sim vs a new one) and see whether their activity distributions genuinely
differ in shape, or whether the threshold jump looks like a rank-based
artifact despite similar-looking data.

USAGE
-----
python3 plot_raster_diagnostic.py path/to/result.h5 --out h_ip_0.07.png
python3 plot_raster_diagnostic.py path/to/other/result.h5 --out h_ip_0.08.png

Then open both PNGs side by side.

Useful flags:
    --window-start / --window-end   analysis window (default matches your
                                     main analysis script: 2,500,000-6,000,000)
    --snippet-start                 offset from window start, for scrolling
                                     to a different part of the run
    --snippet-len                   how many steps to actually plot as a
                                     raster (default 5000 -- plotting millions
                                     of points is unreadable)
    --perc                          the perc value to mark/report (default 0.10)
"""

import argparse

import h5py
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec


def load_snippet(h5path, window_start, window_end, snippet_start, snippet_len):
    with h5py.File(h5path, "r") as f:
        h_ip = float(f["c/h_ip"][0])
        N_steps_total = int(f["c/N_steps"][0])
        if "Spikes" not in f:
            raise ValueError(f"{h5path} has no Spikes dataset -- can't plot a raster.")
        raster_full = f["Spikes"][0]  # (N_e, T_saved)
        saved_start = N_steps_total - raster_full.shape[1]

        a_start = max(window_start - saved_start, 0)
        a_end = min(window_end - saved_start, raster_full.shape[1])
        if a_start >= a_end:
            raise ValueError(f"analysis window [{window_start}:{window_end}] not covered "
                              f"by this file's saved Spikes window [{saved_start}:{N_steps_total}]")
        raster_window = raster_full[:, a_start:a_end].astype(bool)

        s_start = a_start + snippet_start
        s_end = min(s_start + snippet_len, a_end)
        raster_snippet = raster_full[:, s_start:s_end].astype(bool)

    abs_snippet_start = window_start + snippet_start
    return h_ip, raster_window, raster_snippet, abs_snippet_start


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("h5path")
    ap.add_argument("--window-start", type=int, default=2500000)
    ap.add_argument("--window-end", type=int, default=6000000)
    ap.add_argument("--snippet-start", type=int, default=0)
    ap.add_argument("--snippet-len", type=int, default=5000)
    ap.add_argument("--perc", type=float, default=0.10)
    ap.add_argument("--out", default="raster_diagnostic.png")
    args = ap.parse_args()

    h_ip, raster_window, raster_snippet, abs_start = load_snippet(
        args.h5path, args.window_start, args.window_end,
        args.snippet_start, args.snippet_len)

    pop_counts_window = raster_window.sum(axis=0).astype(float)
    pop_counts_snippet = raster_snippet.sum(axis=0).astype(float)

    # Exactly replicates get_avalanches' own perc_threshold computation
    sortN = np.sort(pop_counts_window)
    m = len(pop_counts_window)
    perc_threshold = sortN[round(m * args.perc)]

    frac_at_or_below = float(np.mean(pop_counts_window <= perc_threshold))
    frac_zero = float(np.mean(pop_counts_window == 0))

    print(f"h_ip = {h_ip}")
    print(f"Full analysis window length: {m} steps")
    print(f"perc = {args.perc}  ->  perc_threshold = {perc_threshold}")
    print(f"Fraction of window at or below perc_threshold: {frac_at_or_below:.3f}")
    print(f"Fraction of window at exactly 0: {frac_zero:.3f}")
    print(f"Mean population count: {pop_counts_window.mean():.3f}")
    print(f"Median population count: {np.median(pop_counts_window):.3f}")

    fig = plt.figure(figsize=(14, 8))
    gs = gridspec.GridSpec(3, 1, height_ratios=[3, 1, 1.4], hspace=0.45)

    ax_r = fig.add_subplot(gs[0])
    neuron_ids, spike_times = np.where(raster_snippet)
    ax_r.scatter(spike_times + abs_start, neuron_ids, s=1, c='black', linewidths=0)
    ax_r.set_xlim(abs_start, abs_start + raster_snippet.shape[1])
    ax_r.set_ylabel('Neuron')
    ax_r.set_title(f'h_ip={h_ip}  --  raster snippet [{abs_start}:{abs_start + raster_snippet.shape[1]}]')

    ax_a = fig.add_subplot(gs[1])
    time_axis = np.arange(abs_start, abs_start + len(pop_counts_snippet))
    ax_a.fill_between(time_axis, pop_counts_snippet, color='steelblue', alpha=0.8, linewidth=0)
    ax_a.axhline(perc_threshold, color='red', linestyle='--', linewidth=1.5,
                 label=f'perc_threshold={perc_threshold:.1f}  (perc={args.perc})')
    ax_a.set_ylabel('Spikes/bin')
    ax_a.legend(fontsize=8, loc='upper right')

    ax_h = fig.add_subplot(gs[2])
    max_count = int(pop_counts_window.max())
    ax_h.hist(pop_counts_window, bins=np.arange(0, max_count + 2) - 0.5, color='gray')
    ax_h.axvline(perc_threshold, color='red', linestyle='--', linewidth=1.5)
    ax_h.set_yscale('log')
    ax_h.set_xlabel('Population count (spikes/bin)')
    ax_h.set_ylabel('# timesteps (log)')
    ax_h.set_title('Full analysis-window distribution -- what perc_threshold is actually computed from')

    plt.tight_layout()
    plt.savefig(args.out, dpi=150)
    print(f"Saved to {args.out}")


if __name__ == "__main__":
    main()