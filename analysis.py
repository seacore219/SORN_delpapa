import datetime
import glob
import os
import traceback
 
import nolds
import h5py
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
 
# mrestimator v0.1.8 calls a matplotlib Legend attribute that newer
# matplotlib versions removed -- alias it back so full_analysis() doesn't
# crash while building its (unsaved, since plot_targetdir=None) overview.
import matplotlib.legend as mlegend
if not hasattr(mlegend.Legend, "legendHandles"):
    mlegend.Legend.legendHandles = property(lambda self: self.legend_handles)
 
import criticality_tumbleweed as crt
 
# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
 
SWEEP_ROOT = "backup/test_single"     # "test_single" is now a fixed segment
                                       # in the path itself, not per-run
CONFIG_NAME = "batch_pc"              # matches your actual folder name
BATCH_GLOB = "*_h_ip_0.1"
# RUN_GLOB no longer applies -- this structure has no h_ip_X_runN level;
# each timestamp folder directly under the batch IS one run
TIMESTAMP_FMT = "%Y-%m-%d %H-%M-%S"
 
AV_THRESHOLD_MODE = "perc"      # "adaptive", "perc", or "const"
ADAPTIVE_THRESHOLD_FRAC = 0.5    # only used if AV_THRESHOLD_MODE == "adaptive"
AV_PERC = 0.10               # used only if AV_THRESHOLD_MODE == "perc"
AV_CONST_THRESHOLD = 2      # used only if AV_THRESHOLD_MODE == "const"
 
AV_FLAG = 1                 # 1 = fast (exponents + DCC). 2 = also runs KS p-value tests (slow)
AV_BM = 20                  # AV_analysis: upper limit of xmin search, burst size
AV_TM = 10                  # AV_analysis: upper limit of xmin search, duration
AV_NFACTOR_BM = 0
AV_NFACTOR_TM = 0
AV_NFACTOR_BM_TAIL = 0.75
AV_NFACTOR_TM_TAIL = 0.75
AV_EXCLUDE = True                          # turn on the QC flags (EX_b / EX_t)
AV_EXCLUDE_BURST, AV_EXCLUDE_DIFF_B = 18, 12
AV_EXCLUDE_TIME, AV_EXCLUDE_DIFF_T = 1, 0
 
BRANCHING_KMAX = 50
BRANCHING_FITFUNCS = ["exp", "complex"]    # fitfuncs=None crashes -- must be explicit
 
DFA_NVALS = [int(n) for n in nolds.logarithmic_r(100, 100000, 2.0)]  # ~10 scales, not nolds' default ~65
 
# All generated output lives under SWEEP_ROOT so your .gitignore rule
# already covers it.
OUTPUT_DIR = os.path.join(SWEEP_ROOT, "criticality_analysis_output")
AV_PLOT_DIR = os.path.join(OUTPUT_DIR, "av_plots")
PER_RUN_CSV = os.path.join(OUTPUT_DIR, "criticality_summary_per_run.csv")
BATCH_CSV = os.path.join(OUTPUT_DIR, "criticality_summary_pooled_by_batch.csv")
 
SAVE_AV_PLOTS = True   # set False to skip avalanche distribution plots
 
# Restrict every metric to this absolute step range -- discards the initial
# adaptation transient, keeping only the stabilized-but-still-plastic window.
# Ends at 4,000,000 on purpose: the original frozen-plasticity protocol only
# ever ran plastic dynamics to that step (array indices 4M-6M were a frozen
# replay of t=2M-4M, not new time), so this is the range that is directly
# comparable with earlier batches.
ANALYSIS_WINDOW_START = 2500000
ANALYSIS_WINDOW_END = 4000000

# Derive every metric from `activity` (written by ActivityStat, unchanged from
# the original repo) rather than from the `Spikes` raster, which only newer
# runs contain. Set True to go back to the raster.
USE_RASTER = False
 
# ---------------------------------------------------------------------------
 
 
def find_latest_valid_result(test_single_dir):
    """Return (h5path, timestamp) for the newest attempt folder under
    test_single_dir whose result.h5 actually opens and looks complete, or
    (None, None) if nothing usable is found."""
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
 
    candidates.sort(key=lambda x: x[0], reverse=True)  # newest first
 
    for ts, folder in candidates:
        h5path = os.path.join(folder, "common", "result.h5")
        if not os.path.isfile(h5path):
            continue
        try:
            with h5py.File(h5path, "r") as f:
                if "c" not in f or "N_e" not in f["c"]:
                    continue
                _ = f["c/N_e"][0]  # forces a real read, catches truncated files
            return h5path, ts
        except Exception:
            continue  # corrupt/incomplete attempt -- fall back to older one
 
    return None, None
 
 
def load_sim(h5path):
    with h5py.File(h5path, "r") as f:
        N_e = int(f["c/N_e"][0])
        h_ip = float(f["c/h_ip"][0])
        N_steps_total = int(f["c/N_steps"][0])
        has_raster = USE_RASTER and "Spikes" in f
        raster = f["Spikes"][0].astype(bool) if has_raster else None  # (N_e, T_saved)
        activity = f["activity"][0]  # (N_steps_total,) -- always the full run, starts at step 0
 
    # activity always starts at absolute step 0, so window indices apply directly
    a_start = max(ANALYSIS_WINDOW_START, 0)
    a_end = min(ANALYSIS_WINDOW_END, N_steps_total)
    if a_start >= a_end:
        raise ValueError(f"analysis window [{ANALYSIS_WINDOW_START}:{ANALYSIS_WINDOW_END}] "
                          f"is outside this run's N_steps={N_steps_total}")
    activity = activity[a_start:a_end]
 
    if raster is not None:
        T_saved = raster.shape[1]
        saved_start = N_steps_total - T_saved   # Spikes covers [saved_start, N_steps_total)
        r_start = max(ANALYSIS_WINDOW_START - saved_start, 0)
        r_end = min(ANALYSIS_WINDOW_END - saved_start, T_saved)
        if r_start >= r_end:
            raise ValueError(
                f"analysis window [{ANALYSIS_WINDOW_START}:{ANALYSIS_WINDOW_END}] not covered by "
                f"this run's saved Spikes window [{saved_start}:{N_steps_total}]"
            )
        if (ANALYSIS_WINDOW_START - saved_start) < 0 or (ANALYSIS_WINDOW_END - saved_start) > T_saved:
            print(f"  WARNING: analysis window only partially covered by saved Spikes "
                  f"[{saved_start}:{N_steps_total}] -- using the overlap instead")
        raster = raster[:, r_start:r_end]
 
    return h_ip, N_e, raster, activity
 
 
def get_run_avalanches(h5path):
    """Load one run and return its own avalanche sizes/durations, plus
    everything analyze_one_run() needs for the per-run metrics."""
    h_ip, N_e, raster, activity = load_sim(h5path)
 
    if raster is not None:
        pop_counts = raster.sum(axis=0).astype(float)
    else:
        pop_counts = np.round(activity * N_e).astype(float)
 
    if AV_THRESHOLD_MODE == "adaptive":
        # scales with each run's own activity level, so it means the same
        # thing at h_ip=0.01 as it does at h_ip=0.3, unlike a fixed perc
        thr = max(1, int(round(pop_counts.mean() * ADAPTIVE_THRESHOLD_FRAC)))
        av_kwargs = dict(const_threshold=thr)
    elif AV_THRESHOLD_MODE == "const":
        av_kwargs = dict(const_threshold=AV_CONST_THRESHOLD)
    else:
        av_kwargs = dict(perc=AV_PERC)
 
    if raster is not None:
        av = crt.get_avalanches(raster.astype(float), **av_kwargs)
    else:
        av = crt.get_avalanches(pop_counts, ncells=N_e, **av_kwargs)
 
    return h_ip, N_e, raster, pop_counts, av["S"], av["T"], av["perc_threshold"]
 
 
def run_av_analysis(S, T, pltname):
    return crt.AV_analysis(
        S, T, flag=AV_FLAG, verbose=False,
        plot=SAVE_AV_PLOTS, pltname=pltname, saveloc=AV_PLOT_DIR,
        bm=AV_BM, tm=AV_TM,
        nfactor_bm=AV_NFACTOR_BM, nfactor_tm=AV_NFACTOR_TM,
        nfactor_bm_tail=AV_NFACTOR_BM_TAIL, nfactor_tm_tail=AV_NFACTOR_TM_TAIL,
        exclude=AV_EXCLUDE,
        exclude_burst=AV_EXCLUDE_BURST, exclude_diff_b=AV_EXCLUDE_DIFF_B,
        exclude_time=AV_EXCLUDE_TIME, exclude_diff_t=AV_EXCLUDE_DIFF_T,
    )
 
 
def analyze_one_run(batch_label, run_label, h_ip, N_e, raster, pop_counts, n_avalanches):
    """Per-run metrics -- everything except the pooled avalanche fit."""
    row = {"batch": batch_label, "run": run_label, "h_ip": h_ip, "N_e": N_e,
           "has_full_raster": raster is not None, "n_avalanches": n_avalanches}
 
    br_input = raster.astype(float) if raster is not None else pop_counts
    br = crt.calculate_branching_ratio(
        br_input, k_max=BRANCHING_KMAX, name=run_label,
        fitfuncs=BRANCHING_FITFUNCS, plot_targetdir=None, lreturn_tau=0,
    )
    br_dict = {br[i]: br[i + 1] for i in range(0, len(br), 2)}
    row["branching_ratio_exp"] = br_dict.get("exp")
    row["branching_ratio_complex"] = br_dict.get("complex")
 
    row["dfa"] = nolds.dfa(pop_counts, nvals=DFA_NVALS)
 
    if raster is not None:
        row["branching_parameter"] = crt.branchparam(raster.astype(float))  # copies via astype -- branchparam mutates its input
        susc, fano = crt.population_metrics(raster.astype(float))
        row["susceptibility"] = susc
        row["fano_factor"] = fano
        activity = float(raster.mean())               # mean firing rate per neuron per bin (old's rho())
        row["activity"] = activity
        row["cv"] = (susc ** 0.5) / activity if activity > 0 else None  # old's cv() formula
    else:
        row["branching_parameter"] = None
        row["susceptibility"] = None
        row["fano_factor"] = None
        row["activity"] = None
        row["cv"] = None
 
    return row
 
 
def main():
    os.makedirs(AV_PLOT_DIR, exist_ok=True)
 
    per_run_rows = []
    batch_rows = []
 
    for batch_dir in sorted(glob.glob(os.path.join(SWEEP_ROOT, CONFIG_NAME, BATCH_GLOB))):
        batch_label = os.path.basename(batch_dir)
        print(f"=== batch {batch_label} ===")
 
        batch_S, batch_T = [], []
        batch_h_ip = None
 
        for name in sorted(os.listdir(batch_dir)):
            full = os.path.join(batch_dir, name)
            if not os.path.isdir(full):
                continue
            try:
                datetime.datetime.strptime(name, TIMESTAMP_FMT)
            except ValueError:
                continue  # not a timestamped run folder, skip
            run_label = f"{batch_label}/{name}"
            h5path = os.path.join(full, "common", "result.h5")
            if not os.path.isfile(h5path):
                print(f"  {run_label}: no result.h5, skipping")
                continue
            try:
                with h5py.File(h5path, "r") as f:
                    if "c" not in f or "N_e" not in f["c"]:
                        print(f"  {run_label}: result.h5 missing 'c' group, skipping")
                        continue
                    _ = f["c/N_e"][0]
            except Exception:
                print(f"  {run_label}: result.h5 failed to open, skipping")
                continue
            ts = name
 
            print(f"  --- {run_label}  (using attempt: {ts}) ---")
            try:
                h_ip, N_e, raster, pop_counts, S, T, thr_used = get_run_avalanches(h5path)
                batch_h_ip = h_ip
                batch_S.append(S)
                batch_T.append(T)
 
                row = analyze_one_run(batch_label, run_label, h_ip, N_e, raster, pop_counts, len(S))
                row["av_threshold_used"] = thr_used
                per_run_rows.append(row)
                print(f"    h_ip={h_ip:.3f}  threshold={thr_used}  n_avalanches={len(S)}  "
                      f"branching_ratio_exp={row['branching_ratio_exp']}  dfa={row['dfa']}")
            except Exception:
                print(f"    FAILED on {h5path}:")
                traceback.print_exc()
 
        if not batch_S:
            print(f"  no usable runs in {batch_label}, skipping pooled fit\n")
            continue
 
        pooled_S = np.concatenate(batch_S)
        pooled_T = np.concatenate(batch_T)
        print(f"  pooling {len(batch_S)} runs -> {len(pooled_S)} avalanches total")
 
        try:
            av_res = run_av_analysis(pooled_S, pooled_T, pltname=batch_label + "_pooled_")
            batch_row = {
                "batch": batch_label,
                "h_ip": batch_h_ip,
                "n_runs_pooled": len(batch_S),
                "n_avalanches_pooled": len(pooled_S),
                "alpha_size_exponent": av_res.get("alpha"),
                "beta_duration_exponent": av_res.get("beta"),
                "dcc": av_res.get("df"),
                "xmin_burst": av_res.get("xmin"),
                "xmax_burst": av_res.get("xmax"),
                "xmin_dur": av_res.get("tmin"),
                "xmax_dur": av_res.get("tmax"),
                "excluded_burst_fit": av_res.get("EX_b"),
                "excluded_dur_fit": av_res.get("EX_t"),
            }
            batch_rows.append(batch_row)
            print(f"  pooled fit: alpha={batch_row['alpha_size_exponent']}  "
                  f"beta={batch_row['beta_duration_exponent']}  DCC={batch_row['dcc']}")
        except Exception:
            print(f"  FAILED pooled AV_analysis for {batch_label}:")
            traceback.print_exc()
        print()
 
    if per_run_rows:
        pd.DataFrame(per_run_rows).sort_values(["h_ip", "run"]).to_csv(PER_RUN_CSV, index=False)
        print(f"Saved per-run summary to {PER_RUN_CSV}")
    else:
        print("No runs analyzed successfully.")
 
    if batch_rows:
        pd.DataFrame(batch_rows).sort_values("h_ip").to_csv(BATCH_CSV, index=False)
        print(f"Saved pooled batch summary to {BATCH_CSV}")
    else:
        print("No batches produced a pooled fit.")
 
 
if __name__ == "__main__":
    main()