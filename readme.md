# SORN

This code contains the necessary files to simulate the SORN model and perform the avalanche analysis as described in [Del Papa et al. (2017)](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0178683). It is based on the previous implementation by [Hartmann et al. (2015)](http://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1004640), which can be found [here](https://github.com/chrhartm/SORN).

This model implementation runs in python 2.7 and was my first repository, so it is not very organized, efficient, or easy to clone and run. It also lacks requirements files and installation guides, so version conflicts for some packages are to be expected. Instead of updating and maintaining this old repository, I have decide to create a new (and hopefully better) one. The new implementation in python 3, **SORN_V2** ~~is currently under development~~ can be found [here](https://github.com/delpapa/SORN_V2).

## Experiments

To run simple SORN experiments, proceed in the same way as the original SORN code: navigate to the *common* folder and run `python test_single.py <PATH_TO_PARAM_FILE>`. The simulations store all relevant data in a `backup` folder, which you may change according to your needs.

The avalanche scripts should be run independently of the SORN experiments. They read the simulations results, perform the relevant analysis and plot the avalanche events distributions. Remember to update the simulations folder accordingly (usually named `exper_path` in the scripts).

## Files and data for Del Papa et al. 2017

The experiments and parameter files described in the paper can be found in the `delpapa` folder. The subfolder `avalanches` contains the exact code to reproduce each of the figures, as instructed below. Note that a few parameters must be changed for each of the experiments (for example, in order to reproduce all the simulations for different network sizes from Fig. 2, you should run simulations for each size independently by changing c.N_e in the `param_Zheng2013.py` file).

* Fig. 1:
Parameters and Experiments: `param_Zheng2013.py`, `experiment_Zheng2013.py`.
Figure: `avalanches/Fig1.py`.

* Fig. 2:
Parameters and Experiments: `param_Zheng2013.py`, `experiment_Zheng2013.py`.
Figure: `avalanches/Fig2.py`.

* Fig. 3:
Parameters and Experiments: `param_Zheng2013.py`, `experiment_Zheng2013.py`.
Figure: `avalanches/Fig3.py`.

* Fig. 4:
Parameters and Experiments: `param_FrozenPlasticity.py`, `experiment_FrozenPlasticity.py`.
Figure: `avalanches/Fig4.py`.

* Fig. 5:
Parameters and Experiments: `param_Zheng2013.py`, `experiment_Zheng2013.py`.
Figure: `avalanches/Fig5.py`.

* Fig. 6:
Parameters and Experiments: `param_ExtraInput.py`, `experiment_ExtraInput.py`.
Figure: `avalanches/Fig6.py`.

* Fig. 7:
Parameters and Experiments: `param_CountingTask.py`, `experiment_CountingTask.py` and `param_RandomTask.py`, `experiment_RandomTask.py`.
Figure: `avalanches/Fig7.py`.


## Frozen plasticity: perturbation currently disabled

`experiment_FrozenPlasticity.py` is set up to run as a **standard, continuous
SORN simulation**: the three `sorn.simulation()` calls in `run()` add up to one
uninterrupted `c.N_steps` run with every plasticity mechanism active. The
network snapshot at `c.steps_plastic` (`net_before_pert.pickle`) is still
written, but the reload, the re-seeding and the five freezing assignments are
commented out with `#~`. To restore the protocol of Del Papa et al. (2017),
uncomment every `#~` line in `run()` (and nothing else), and switch the
`plot_results_single` import at the top of the file back to
`plot_results_perturbation`.

The experiment now records, for the whole simulation:

| h5 array | stat | shape |
| --- | --- | --- |
| `activity`, `activityInh` | `ActivityStat`, `ActivityInhibStat` | `N_steps` |
| `Spikes` | `SpikesStat` | `N_e x N_steps` |
| `SpikesInh` | `SpikesInhStat` | `N_i x N_steps` |
| `ConnectionFraction` | `ConnectionFractionStat` | `N_steps` |
| `endweight` | `EndWeightStat` | `W_ee`, `N_e x N_e` |
| `FullEndWeight` | `FullEndWeightStat` | `[[W_ee, W_ei], [W_ie, 0]]`, `N x N` |

Every spike is kept because `param_FrozenPlasticity.py` sets
`c.stats.only_last_spikes = c.N_steps`. **This is memory hungry**: the spike
arrays are float64, so a 6M step run of 200 excitatory and 40 inhibitory
neurons needs about **11.6 GB of RAM** (9.6 GB for `Spikes`, 1.9 GB for
`SpikesInh`), allocated up front in `stats.start()` — a machine without room
fails in the first seconds rather than hours in. Run these sequentially
(`--jobs 1`). On disk the h5 is zlib compressed, so the file is much smaller.
If you override `c.N_steps` (or the `steps_*` parameters) from the sweep
driver, repeat `c.stats.only_last_spikes = c.N_steps` with `--exec`; the driver
warns about this.

Because every spike is saved, `plot_single` uses `delpapa/plot_standard.py`
instead of `plot.py`: it saves the connection fraction and activity over the
whole run, but limits the rasters to the last `c.stats.raster_steps` (default
1000) steps. `plot.py` is unchanged — rastering 6M steps would hand matplotlib
~10^8 line segments.

## Parameter sweeps

`run_sweep.py` (in the repository root) runs `common/test_single.py` repeatedly
while sweeping one or more parameters. The driver itself is **python 3** and
only uses the standard library; each simulation is launched as a subprocess
with a python 2 interpreter (auto-detected, or given with `--python`), since
the model code is python 2.7.

Sweep `h_ip` from 0.02 to 0.2 in steps of 0.02 (10 simulations) with the frozen
plasticity parameters and experiment -- this is what the defaults do:

```
python3 run_sweep.py
python3 run_sweep.py --param delpapa.param_FrozenPlasticity --sweep h_ip=0.02:0.2:0.02
```

Useful options (`python3 run_sweep.py --help` lists them all):

* `--sweep NAME=SPEC` -- `start:stop:step` (stop included), a comma separated
  list, or a single value. Repeat the option to sweep several parameters; all
  combinations are run. Dotted names address sub-bunches, e.g.
  `--sweep W_ee.eta_stdp=0.001,0.004`. Sweeping `h_ip` sets both `c.h_ip` and
  `c.W_ei.h_ip`, as the param files do.
* `--repeats N` -- simulations per parameter value.
* `--jobs N` -- simulations to run concurrently.
* `--set NAME=VALUE` -- constant override for every run, e.g.
  `--set N_steps=100000` for a quick test.
* `--exec CODE` -- extra python line for every run, to recompute quantities the
  param file derives from a swept parameter, e.g.
  `--exec 'c.N_steps = c.steps_plastic + 2*c.steps_perturbation'`.
* `--progress-interval SECONDS` -- how often the progress line refreshes
  (default: 1 on a terminal, 60 when redirected, e.g. under `nohup`); `0`
  turns it off.
* `--dry-run` -- show the planned runs and the generated parameters.
* `--resume` -- reuse a sweep directory, skipping runs that already finished.

Nothing in the existing code is modified: for every sweep point the driver
generates a small module in `sweep_params/` that imports the base parameter
file and overrides only the swept values, and passes it to `test_single.py`
like a hand written parameter file.

While the sweep runs, a status line shows the number of finished simulations,
the phase and percentage of the running ones, the elapsed time and an ETA:

```
[3/10 done] h_ip_0.06 r1 phase 2 47% 21m14s (ETA 24m) · sweep 1h02m · ETA 2h30m, done ~16:41
```

The percentage comes from the `Simulation: NN%` message `common/sorn.py`
already prints while `c.display` is True (all `delpapa` param files set it), so
no simulation code is involved. The sweep ETA appears once the first run has
finished, since that is the first measurement of how long a run takes.

Results are collected in `backup/sweeps/<sweep>/<value>/<repetition>/common/result.h5`
(repetitions numbered from 1), which is the layout the avalanche scripts
expect: point their `exper_path` at `backup/sweeps/<sweep>/<value>/` and set
`number_of_files` to the number of repetitions. `--layout raw` leaves the
backup directories where `test_single.py` created them instead. Every sweep
directory also holds a `manifest.json` with the parameters, status, runtime and
output directory of each run, plus the full simulation output in `logs/`.

## Dependencies

This code relies on the [powerlaw](https://pypi.python.org/pypi/powerlaw) python package to fit the power-law distributions of avalanche sizes and durations.
