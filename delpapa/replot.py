####
# Regenerate the figures of finished runs from their result.h5.
#
# The simulation does not have to be repeated: everything the plots need is
# already in the h5 file. Useful when a run wrote its results but died before
# plot_single (see the quicksave note in experiment_FrozenPlasticity.run).
#
# Usage, from the 'common' folder:
#
#   python ../delpapa/replot.py <run directory> [<run directory> ...]
#   python ../delpapa/replot.py <sweep directory>/manifest.json
#
# A run directory is one that contains common/result.h5; the figures are
# written to <run directory>/plots, exactly where a successful run puts them.
####

from __future__ import division
import matplotlib
matplotlib.use('Agg')      # no windows, this is a batch script

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import utils
from pylab import savefig, close

# The backup machinery is not initialised here, so send the figures straight
# into the current directory, which plot_results_standard sets to <run>/plots.
utils.saveplot = lambda figurename, f=None: savefig(figurename, dpi=300)

from delpapa.plot_standard import plot_results_standard


def run_dirs_from_manifest(path):
    """Every run directory recorded in a run_sweep.py manifest."""
    manifest = json.load(open(path))
    directories = []
    for record in manifest.get('runs', []):
        directory = record.get('result_dir') or record.get('backup_dir')
        if directory and os.path.isdir(os.path.join(directory, 'common')):
            directories.append(directory)
    return directories


def replot(run_dir):
    result = os.path.join(run_dir, 'common', 'result.h5')
    if not os.path.isfile(result):
        print 'no result.h5 in %s - skipped' % run_dir
        return False
    here = os.getcwd()
    try:
        # plot_results_standard() writes into '../plots' relative to the
        # current directory, so run it from <run_dir>/common.
        os.chdir(os.path.join(run_dir, 'common'))
        plot_results_standard('.', 'result.h5')
        close('all')
    finally:
        os.chdir(here)
    print 'figures written to %s' % os.path.join(run_dir, 'plots')
    return True


def main(arguments):
    if not arguments:
        print __doc__ or 'usage: replot.py <run directory|manifest.json> ...'
        return 1
    targets = []
    for argument in arguments:
        if argument.endswith('.json'):
            targets.extend(run_dirs_from_manifest(argument))
        else:
            targets.append(argument)
    done = 0
    for target in targets:
        print '\n--- %s' % target
        if replot(os.path.abspath(target)):
            done += 1
    print '\nreplotted %d of %d run(s)' % (done, len(targets))
    return 0 if done == len(targets) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
