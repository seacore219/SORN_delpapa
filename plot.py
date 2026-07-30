"""
Plot summary statistics across the h_ip sweep, reading the CSVs produced by
run_criticality_analysis.py. Styled after the old plot.py: one datapoint per
batch (xx_xx_xx_h_ip_0.xx folder), black line+dot per metric, semi-transparent
gray error-bar boxes spanning mean +/- std across that batch's individual runs.

METRIC MAPPING (see chat -- flag if this isn't what you meant):
    avalanche S     -> alpha_size_exponent      (pooled fit, no error bars --
    avalanche T     -> beta_duration_exponent      it's one fit per batch,
    avalanche gamma -> dcc                         not per-run)
    branching ratio, 3 ways -> branching_parameter, branching_ratio_exp,
                                branching_ratio_complex  (per-run, has error bars)
    susceptibility, activity, cv, fano_factor, dfa -> per-run, has error bars

Requires: pandas, numpy, matplotlib

USAGE
-----
Run from the SORN_delpapa repo root (or edit SWEEP_ROOT/CONFIG_NAME below to
match whichever batch of runs you want to summarize):
    python3 plot_summary.py
"""

import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

# ---------------------------------------------------------------------------
# CONFIG -- must match the SWEEP_ROOT/CONFIG_NAME used in run_criticality_analysis.py
# ---------------------------------------------------------------------------

SWEEP_ROOT = "nrp-sweep-data"
CONFIG_NAME = "test_sweep"

OUTPUT_DIR = os.path.join(SWEEP_ROOT, CONFIG_NAME, "criticality_analysis_output")
PER_RUN_CSV = os.path.join(OUTPUT_DIR, "criticality_summary_per_run.csv")
BATCH_CSV = os.path.join(OUTPUT_DIR, "criticality_summary_pooled_by_batch.csv")

SUMMARY_PDF = os.path.join(OUTPUT_DIR, "sweep_summary.pdf")

# Per-run metrics: mean +/- std across the runs in each batch
PER_RUN_METRICS = {
    "activity":               "Activity (\u03c1)",
    "susceptibility":         "Susceptibility",
    "cv":                     "CV",
    "fano_factor":            "Fano factor",
    "branching_parameter":    "Branch param",
    "branching_ratio_exp":    "BR (exp fit)",
    "branching_ratio_complex": "BR (complex fit)",
    "dfa":                    "DFA",
}

# Pooled-fit metrics: one value per batch, no error bars
POOLED_METRICS = {
    "alpha_size_exponent":    "AVsize (\u03b1)",
    "beta_duration_exponent": "AVduration (\u03b2)",
    "dcc":                    "Scaling diff (\u03b3)",
}

FIXED_WIDTH_FRAC = 0.05   # error-bar box half-width, as a fraction of x position

# ---------------------------------------------------------------------------


def style_axis(ax):
    ax.set_facecolor('white')
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_linewidth(1.5)
        spine.set_color('#2C2C2C')
    ax.tick_params(axis='both', which='major', labelsize=8, width=1.2, length=4)


def plot_metric(ax, x, y, yerr, title):
    order = np.argsort(x)
    x, y = np.asarray(x)[order], np.asarray(y)[order]
    ax.plot(x, y, 'ko-', markersize=5, linewidth=1.2, markeredgecolor='white',
             markeredgewidth=0.5, zorder=10)

    if yerr is not None:
        yerr = np.asarray(yerr)[order]
        for xi, yi, ei in zip(x, y, yerr):
            if ei is None or np.isnan(ei):
                continue
            xl = xi * (1 - FIXED_WIDTH_FRAC)
            xr = xi * (1 + FIXED_WIDTH_FRAC)
            ax.add_patch(plt.Rectangle((xl, yi - ei), xr - xl, 2 * ei,
                                        facecolor='black', alpha=0.15,
                                        edgecolor='none', zorder=5))

    ax.set_title(title, fontsize=11, fontweight='bold', pad=6)
    ax.set_xlabel('target firing rate', fontsize=9, labelpad=2)
    style_axis(ax)
    ax.set_xticks(x)
    ax.set_xticklabels([f'{v:.4g}' for v in x], rotation=45, ha='right', fontsize=7)


def main():
    matplotlib.rcParams['font.family'] = 'Arial'

    if not os.path.isfile(PER_RUN_CSV):
        print(f"Missing {PER_RUN_CSV} -- run run_criticality_analysis.py first.")
        return
    if not os.path.isfile(BATCH_CSV):
        print(f"Missing {BATCH_CSV} -- run run_criticality_analysis.py first.")
        return

    per_run = pd.read_csv(PER_RUN_CSV)
    batch = pd.read_csv(BATCH_CSV).sort_values('h_ip')

    if 'batch' not in per_run.columns:
        print("criticality_summary_per_run.csv has no 'batch' column -- "
              "update analyze_one_run()/main() in run_criticality_analysis.py "
              "to add it (see chat), then rerun the analysis before plotting.")
        return

    # Aggregate per-run metrics to one mean/std per batch (== one h_ip value)
    agg = per_run.groupby(['batch', 'h_ip'])[list(PER_RUN_METRICS.keys())] \
                 .agg(['mean', 'std']).reset_index()
    agg.columns = ['batch', 'h_ip'] + [f'{m}_{stat}' for m in PER_RUN_METRICS
                                        for stat in ('mean', 'std')]
    agg = agg.sort_values('h_ip')

    all_metrics = list(PER_RUN_METRICS.items()) + list(POOLED_METRICS.items())
    n = len(all_metrics)
    n_cols = 4
    n_rows = (n + n_cols - 1) // n_cols

    with PdfPages(SUMMARY_PDF) as pdf:
        # --- page 1: grid of all metrics vs h_ip ---
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(4.2 * n_cols, 4.0 * n_rows))
        fig.suptitle('Criticality metrics vs h_ip', fontsize=15, y=0.995)
        axes_flat = axes.flatten()

        for i, (col, title) in enumerate(all_metrics):
            ax = axes_flat[i]
            if col in PER_RUN_METRICS:
                plot_metric(ax, agg['h_ip'], agg[f'{col}_mean'], agg[f'{col}_std'], title)
            else:
                plot_metric(ax, batch['h_ip'], batch[col], None, title)

        for i in range(n, len(axes_flat)):
            axes_flat[i].set_visible(False)

        plt.tight_layout()
        pdf.savefig(fig, bbox_inches='tight')
        plt.close()

        # --- page 2: scaling line (AVsize alpha vs AVduration beta) ---
        if {'alpha_size_exponent', 'beta_duration_exponent'}.issubset(batch.columns):
            fig, ax = plt.subplots(figsize=(6, 5.5))
            sc = ax.scatter(batch['alpha_size_exponent'], batch['beta_duration_exponent'],
                             c=batch['h_ip'], cmap='plasma', s=140,
                             edgecolor='black', linewidth=0.8, zorder=5)

            if len(batch) >= 2:
                slope, intercept = np.polyfit(batch['alpha_size_exponent'],
                                               batch['beta_duration_exponent'], 1)
                x_line = np.linspace(batch['alpha_size_exponent'].min() - 0.2,
                                      batch['alpha_size_exponent'].max() + 0.2, 100)
                ax.plot(x_line, slope * x_line + intercept, 'k--', linewidth=1.5,
                        label=f'slope = {slope:.3f}', zorder=3)
                ax.legend(fontsize=9, loc='best')

            cb = plt.colorbar(sc, ax=ax, ticks=sorted(batch['h_ip'].unique()))
            cb.set_label('target firing rate', fontsize=10)
            ax.set_xlabel('AVsize exponent (\u03b1)', fontsize=11)
            ax.set_ylabel('AVduration exponent (\u03b2)', fontsize=11)
            ax.set_title('Scaling line', fontsize=13, fontweight='bold')
            style_axis(ax)

            plt.tight_layout()
            pdf.savefig(fig, bbox_inches='tight')
            plt.close()

    print(f"Saved summary to {SUMMARY_PDF}")


if __name__ == "__main__":
    main()