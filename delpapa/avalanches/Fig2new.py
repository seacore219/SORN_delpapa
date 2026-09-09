"""
Reproduces panels A, B, C of the original paper's Figure 2 (duration
exponent, size exponent, and the alpha/tau scaling ratio) using your h_ip
sweep data.

NOT reproduced: panels D, E, F. Those require simulations at multiple
network sizes (N=50, 100, 200, 400, 800); your sweep is fixed at N=200
across different h_ip values, so there's no data to reproduce them from.

DIFFERENT ALGORITHM FROM YOUR MAIN PIPELINE (run_criticality_analysis.py):
This uses the original paper's own avalanche-detection logic
(data_analysis.avalanches(), ported here to Python 3 -- the original has
Python 2 print statements and would crash outright as-is), NOT
criticality_tumbleweed's get_avalanches. Two concrete differences:
  - threshold = half the GLOBAL mean activity across all loaded runs
    combined (not per-run, not adaptive to each individual run)
  - no gap-merging: a single timestep dipping at/below threshold ends an
    avalanche immediately (get_avalanches bridges single-step dips)
Don't expect these numbers to match your main pipeline's output -- this is
intentionally using the paper's original method for a faithful
reproduction, not your current pipeline's method.

WINDOW: steps [3,000,000:6,000,000) -- the last 3,000,000 steps that are
still in the plastic phase (excludes your 500,000-step frozen tail, which
the paper's literal "last 3M steps" convention would otherwise include).

FIT RANGES: T_XMIN/T_XMAX and S_XMIN/S_XMAX below are the ORIGINAL PAPER's
calibrated values, not necessarily right for your h_ip=0.1 data. The script
prints your actual duration/size ranges so you can sanity-check these
before trusting the fit -- adjust them if your data's range looks very
different from what's configured.

Requires: h5py, numpy, powerlaw (pip3 install powerlaw), matplotlib
"""

import os
import glob
import datetime

import numpy as np
import h5py
import matplotlib
matplotlib.use('Agg')
from matplotlib import gridspec
from matplotlib.pyplot import (figure, subplot, plot, xscale, yscale, xlabel,
                                ylabel, legend, tick_params, savefig)
import powerlaw as pl

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

H_IP_DIR = "/Applications/SORN/SORN_delpapa/nrp-sweep-data/batch_0.01_0.01_0.3/09_03_26_h_ip_0.1"
# H_IP_DIR = "/Applications/SORN/SORN_delpapa/nrp-sweep-data/batch_0.01_0.01_0.3/07_29_26_h_ip_0.1"  # old nested-structure sweep data
RUN_GLOB = "h_ip_*_run*"
TIMESTAMP_FMT = "%Y-%m-%d %H-%M-%S"

WINDOW_START = 3000000
WINDOW_END = 6000000

OUTPUT_PDF = "Fig2_h_ip_0.1.pdf"

# Original paper's calibrated fit ranges -- check the printed data range
# against these before trusting the result.
T_XMIN, T_XMAX = 6, 60
S_XMIN, S_XMAX = 10, 1500

# ---------------------------------------------------------------------------


def find_latest_valid_result(test_single_dir):
    """Same convention as run_criticality_analysis.py: newest timestamped
    attempt whose result.h5 actually opens, falling back to older ones."""
    candidates = []
    for name in os.listdir(test_single_dir):
        full = os.path.join(test_single_dir, name)
        if not os.path.isdir(full):
            continue
        try:
            ts = datetime.datetime.strptime(name, TIMESTAMP_FMT)
        except ValueError:
            continue
        candidates.append((ts, full))
    candidates.sort(key=lambda x: x[0], reverse=True)

    for ts, folder in candidates:
        h5path = os.path.join(folder, "common", "result.h5")
        if not os.path.isfile(h5path):
            continue
        try:
            with h5py.File(h5path, "r") as f:
                if "c" not in f or "N_e" not in f["c"]:
                    continue
                _ = f["c/N_e"][0]
            return h5path, ts
        except Exception:
            continue
    return None, None


# --- OLD nested structure: <h_ip_dir>/h_ip_X_runN/test_single/<timestamp>/common/result.h5
# def gather_run_files(h_ip_dir):
#     h5_paths = []
#     for run_dir in sorted(glob.glob(os.path.join(h_ip_dir, RUN_GLOB))):
#         test_single_dir = os.path.join(run_dir, "test_single")
#         if not os.path.isdir(test_single_dir):
#             print(f"  {os.path.basename(run_dir)}: no test_single/ folder, skipping")
#             continue
#         h5path, ts = find_latest_valid_result(test_single_dir)
#         if h5path is None:
#             print(f"  {os.path.basename(run_dir)}: no usable result.h5, skipping")
#             continue
#         h5_paths.append(h5path)
#     return h5_paths


# --- NEW flat structure: <h_ip_dir>/<timestamp>/common/result.h5 -- each
# timestamped folder directly under h_ip_dir is one independent run, no
# h_ip_X_runN/test_single wrapping and no retry-folder deduplication (every
# timestamp found is assumed to be a distinct, real run).
def gather_run_files(h_ip_dir):
    h5_paths = []
    for name in sorted(os.listdir(h_ip_dir)):
        full = os.path.join(h_ip_dir, name)
        if not os.path.isdir(full):
            continue
        try:
            datetime.datetime.strptime(name, TIMESTAMP_FMT)
        except ValueError:
            continue  # not a timestamped run folder (e.g. a stray file), skip
        h5path = os.path.join(full, "common", "result.h5")
        if not os.path.isfile(h5path):
            print(f"  {name}: no result.h5, skipping")
            continue
        try:
            with h5py.File(h5path, "r") as f:
                if "c" not in f or "N_e" not in f["c"]:
                    print(f"  {name}: result.h5 missing 'c' group, skipping")
                    continue
                _ = f["c/N_e"][0]
        except Exception:
            print(f"  {name}: result.h5 failed to open, skipping")
            continue
        h5_paths.append(h5path)
    return h5_paths


def load_pop_counts(h5path):
    with h5py.File(h5path, "r") as f:
        N_e = int(f["c/N_e"][0])
        N_steps_total = int(f["c/N_steps"][0])
        activity = f["activity"][0]
    a_start = max(WINDOW_START, 0)
    a_end = min(WINDOW_END, N_steps_total)
    if a_start >= a_end:
        raise ValueError(f"window [{WINDOW_START}:{WINDOW_END}] outside "
                          f"this run's N_steps={N_steps_total}")
    return np.round(activity[a_start:a_end] * N_e)


def compute_avalanches(activity_matrix, Theta):
    """Port of data_analysis.avalanches() for Threshold=<precomputed Theta>,
    binsize=False, fullS=False, Transient=0 -- the exact call Fig2.py makes.
    Returns (durations, areas), matching the original's (T, S) return order."""
    all_durations = []
    all_areas = []
    for row in activity_matrix:
        shifted = row - Theta
        above = shifted > 0
        padded = np.concatenate(([False], above, [False]))
        diff = np.diff(padded.astype(int))
        starts = np.where(diff == 1)[0]
        ends = np.where(diff == -1)[0]
        for s, e in zip(starts, ends):
            all_durations.append(e - s)
            all_areas.append(int(shifted[s:e].sum()))
    return np.asarray(all_durations), np.asarray(all_areas)


def area_x_duration(a_dur, a_area):
    """Equivalent to the original's area_X_duration(): one (duration, mean
    area) point per unique duration value seen in the data."""
    unique_durs = np.unique(a_dur)
    S_avg = np.array([a_area[a_dur == d].mean() for d in unique_durs])
    return unique_durs, S_avg


def main():
    print(f"Searching {H_IP_DIR} ...")
    h5_paths = gather_run_files(H_IP_DIR)
    print(f"Found {len(h5_paths)} usable run(s)")
    if not h5_paths:
        print("Nothing to do.")
        return

    print("Loading activity and building population-count matrix...")
    rows = [load_pop_counts(p) for p in h5_paths]
    lengths = {len(r) for r in rows}
    if len(lengths) > 1:
        min_len = min(lengths)
        print(f"WARNING: runs have different window lengths {lengths} -- "
              f"truncating all to {min_len} so they share one array")
        rows = [r[:min_len] for r in rows]
    data_all = np.array(rows)
    print(f"data_all shape: {data_all.shape}")

    # global threshold = half the mean, across ALL loaded runs combined
    # (matches data_analysis.avalanches(..., Threshold='half') exactly)
    Theta = int(data_all.mean() / 2.)
    print(f"Theta (half of global mean activity) = {Theta}")

    T_data, S_data = compute_avalanches(data_all, Theta)
    print(f"Found {len(T_data)} avalanches")
    print(f"Duration range in data: {T_data.min()}-{T_data.max()}  "
          f"(fit range configured: [{T_XMIN}, {T_XMAX}])")
    print(f"Size range in data: {S_data.min()}-{S_data.max()}  "
          f"(fit range configured: [{S_XMIN}, {S_XMAX}])")

    # -----------------------------------------------------------------
    # Figure (matches Fig2.py's A/B/C styling; axis limits/ticks are left
    # to auto-scale rather than using the paper's hardcoded values, since
    # those were tuned to the paper's own data range, not necessarily yours)
    # -----------------------------------------------------------------
    width = 8
    height = width / 1.718
    fig = figure(1, figsize=(width, height))
    gs = gridspec.GridSpec(2, 3)
    letter_size = 10
    letter_size_panel = 12
    line_width = 1.5
    line_width_fit = 2.0
    subplot_letter = (-0.25, 1.15)

    c_size = '#B22400'
    c_duration = '#006BB2'
    c_rawdata = 'gray'
    c_expcut = 'k'

    # --- Panel A: duration distribution ---
    fig_2a = subplot(gs[0])
    T_x, inverse = np.unique(T_data, return_inverse=True)
    y_freq = np.bincount(inverse)
    T_y = y_freq / float(y_freq.sum())
    plot(T_x, T_y, '.', color=c_rawdata, markersize=2, zorder=1)

    T_fit = pl.Fit(T_data, xmin=T_XMIN, xmax=T_XMAX, discrete=True)
    T_alpha = T_fit.power_law.alpha

    # Diagnostic: what does an UNCONSTRAINED fit (powerlaw finds its own
    # optimal xmin via KS-minimization, no fixed xmax) say about this data?
    # If this gives a sensible, non-boundary alpha while the fixed-window
    # fit above pins at ~1.0, that's strong evidence the paper's [T_XMIN,
    # T_XMAX] window is simply wrong for this dataset, not that the
    # underlying distribution/dynamics are broken.
    T_fit_auto = pl.Fit(T_data, discrete=True)
    print(f"\n[DIAGNOSTIC] Duration (T): fixed window [{T_XMIN},{T_XMAX}] -> alpha={T_alpha:.3f}")
    print(f"[DIAGNOSTIC] Duration (T): auto xmin={T_fit_auto.xmin} (no xmax) -> alpha={T_fit_auto.power_law.alpha:.3f}")
    T_fit.power_law.plot_pdf(color=c_duration,
                              label=r'$ \alpha = $' + str(round(T_alpha, 2)),
                              linewidth=line_width_fit, zorder=3)

    T_fit_full = pl.Fit(T_data, xmin=T_XMIN, discrete=True)
    T_trunc_alpha = T_fit_full.truncated_power_law.parameter1
    T_trunc_beta = T_fit_full.truncated_power_law.parameter2
    T_fit_full.truncated_power_law.plot_pdf(
        color=c_expcut,
        label=r'$ \alpha^* = $' + str(round(T_trunc_alpha, 2)) + ', ' +
              r'$ \beta_{\alpha}^* = $' + str(round(T_trunc_beta, 3)),
        linewidth=line_width, zorder=2)

    xscale('log'); yscale('log')
    xlabel(r'$T$', fontsize=letter_size)
    ylabel(r'$f(T)$', fontsize=letter_size)
    fig_2a.spines['right'].set_visible(False)
    fig_2a.spines['top'].set_visible(False)
    fig_2a.xaxis.set_ticks_position('bottom')
    fig_2a.yaxis.set_ticks_position('left')
    tick_params(labelsize=letter_size)
    legend(loc=(0.0, 0.85), prop={'size': letter_size}, title='Fit parameters', frameon=False)
    fig_2a.get_legend().get_title().set_fontsize(letter_size)
    fig_2a.annotate('A', xy=subplot_letter, xycoords='axes fraction',
                     fontsize=letter_size_panel, fontweight='bold',
                     horizontalalignment='right', verticalalignment='bottom')

    # --- Panel B: size distribution ---
    fig_2b = subplot(gs[1])
    S_x, inverse = np.unique(S_data, return_inverse=True)
    y_freq = np.bincount(inverse)
    S_y = y_freq / float(y_freq.sum())
    plot(S_x, S_y, '.', color=c_rawdata, markersize=2, zorder=1)

    S_fit = pl.Fit(S_data, xmin=S_XMIN, xmax=S_XMAX, discrete=True)
    S_alpha = S_fit.power_law.alpha

    S_fit_auto = pl.Fit(S_data, discrete=True)
    print(f"[DIAGNOSTIC] Size (S): fixed window [{S_XMIN},{S_XMAX}] -> alpha={S_alpha:.3f}")
    print(f"[DIAGNOSTIC] Size (S): auto xmin={S_fit_auto.xmin} (no xmax) -> alpha={S_fit_auto.power_law.alpha:.3f}\n")
    S_fit.power_law.plot_pdf(color=c_size,
                              label=r'$ \tau = $' + str(round(S_alpha, 2)),
                              linewidth=line_width_fit, zorder=3)

    S_fit_full = pl.Fit(S_data, xmin=S_XMIN, discrete=True)
    S_trunc_alpha = S_fit_full.truncated_power_law.parameter1
    S_trunc_beta = S_fit_full.truncated_power_law.parameter2
    S_fit_full.truncated_power_law.plot_pdf(
        color=c_expcut,
        label=r'$ \tau^* = $' + str(round(S_trunc_alpha, 2)) + '; ' +
              r'$\beta_{\tau}^* = $' + str(round(S_trunc_beta, 3)),
        linewidth=line_width, zorder=2)

    xscale('log'); yscale('log')
    xlabel(r'$S$', fontsize=letter_size)
    ylabel(r'$f(S)$', fontsize=letter_size)
    fig_2b.spines['right'].set_visible(False)
    fig_2b.spines['top'].set_visible(False)
    fig_2b.xaxis.set_ticks_position('bottom')
    fig_2b.yaxis.set_ticks_position('left')
    tick_params(labelsize=letter_size)
    legend(loc=(0.0, 0.85), prop={'size': letter_size}, title='Fit parameters', frameon=False)
    fig_2b.get_legend().get_title().set_fontsize(letter_size)
    fig_2b.annotate('B', xy=subplot_letter, xycoords='axes fraction',
                     fontsize=letter_size_panel, fontweight='bold',
                     horizontalalignment='right', verticalalignment='bottom')

    print("\nDuration (T) power-law vs alternatives:")
    print("  vs exponential:", T_fit.distribution_compare('power_law', 'exponential', normalized_ratio=True))
    print("  vs stretched exponential:", T_fit.distribution_compare('power_law', 'stretched_exponential', normalized_ratio=True))
    print("\nSize (S) power-law vs alternatives:")
    print("  vs exponential:", S_fit.distribution_compare('power_law', 'exponential', normalized_ratio=True))
    print("  vs stretched exponential:", S_fit.distribution_compare('power_law', 'stretched_exponential', normalized_ratio=True))

    # --- Panel C: scaling ratio ---
    fig_2c = subplot(gs[2])
    a_dur_avg, a_area_avg = area_x_duration(T_data, S_data)
    plot(a_dur_avg, a_area_avg / a_area_avg.sum(), '.', color=c_rawdata,
         markersize=2, zorder=1, label=r'$\gamma_{\rm data}$')

    x_range = np.arange(1, a_dur_avg.max())
    gamma = (T_alpha - 1) / (S_alpha - 1)
    plot(x_range, (a_area_avg / a_area_avg.sum()).min() * x_range ** gamma, 'r',
         label=r'$ \frac{\alpha-1}{\tau-1} $ = ' + str(round(gamma, 2)), linewidth=line_width)
    plot(x_range, (a_area_avg / a_area_avg.sum()).min() * x_range ** 1.3, '--k',
         label=r'$\gamma = $' + str(1.3), linewidth=line_width)

    xscale('log'); yscale('log')
    xlabel(r'$T$', fontsize=letter_size)
    ylabel(r'$ \langle S \rangle (T)$', fontsize=letter_size)
    fig_2c.spines['right'].set_visible(False)
    fig_2c.spines['top'].set_visible(False)
    fig_2c.xaxis.set_ticks_position('bottom')
    fig_2c.yaxis.set_ticks_position('left')
    tick_params(labelsize=letter_size)
    legend(loc=(0.0, 0.65), prop={'size': letter_size}, title='Exponent ratio', frameon=False, numpoints=1)
    fig_2c.get_legend().get_title().set_fontsize(letter_size)
    fig_2c.annotate('C', xy=subplot_letter, xycoords='axes fraction',
                     fontsize=letter_size_panel, fontweight='bold',
                     horizontalalignment='right', verticalalignment='bottom')

    fig.subplots_adjust(wspace=.5)
    savefig(OUTPUT_PDF, format='pdf', bbox_inches='tight')
    print(f"\nSaved {OUTPUT_PDF}")


if __name__ == "__main__":
    main()