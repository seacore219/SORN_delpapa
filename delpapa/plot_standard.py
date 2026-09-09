####
# Plots for continuous (non-perturbation) SORN runs that save every spike.
#
# delpapa/plot.py rasters the whole saved spike train, which is fine while
# c.stats.only_last_spikes is small but impossible once every spike of a
# multi-million step simulation is kept: it would slice several GB out of the
# h5 file and hand matplotlib ~10^8 line segments. The functions here plot the
# full traces of the cheap statistics and restrict the rasters to the last
# c.stats.raster_steps steps.
#
# delpapa/plot.py is left untouched, so the frozen plasticity figures of
# Del Papa et al. (2017) are still produced by plot_results_perturbation.
####

from __future__ import division
from pylab import *
import tables

import sys
sys.path.insert(0,"../")
import utils
utils.backup(__file__)

import os

matplotlib.rcParams.update({'font.size': 10})

DEFAULT_RASTER_STEPS = 1000


def _raster_steps(data):
    """Number of final steps to raster, from c.stats.raster_steps."""
    if data.c.stats.__contains__('raster_steps'):
        return int(data.c.stats.raster_steps[0])
    return DEFAULT_RASTER_STEPS


def _raster(spikes, activity, neuron_label, activity_label, filename):
    """Raster of a (neurons, steps) spike block, with activity underneath."""
    figure()
    if activity is not None:
        subplot(211)
    i = -1
    for (i, sp) in enumerate(spikes):
        s_train = where(sp == 1)[0]
        if len(s_train) > 0:
            vlines(s_train, i + 0.5, i + 1.5)
    ylabel(neuron_label)
    if i >= 0:
        ylim(0.5, i + 1.5)
    if activity is not None:
        subplot(212)
        plot(activity, 'k')
        xlabel('Step'); ylabel(activity_label)
    tight_layout()
    utils.saveplot(filename)


def plot_results_standard(result_path, result):
    h5 = tables.open_file(os.path.join(result_path, result), 'r')
    data = h5.root

    plots_path = os.path.join('..', 'plots')
    if not os.path.exists(plots_path):
        os.mkdir(plots_path)
    os.chdir(plots_path)

    last_steps = _raster_steps(data)

    ### Fraction of E-E connections over the whole simulation
    if data.__contains__('ConnectionFraction'):
        print 'plot connectionfraction'
        figure()
        plot(data.ConnectionFraction[0][:data.c.N_steps[0]])
        xlabel('Time Step'); ylabel('Fraction of E-E connections')
        tight_layout()
        utils.saveplot('ConnectionFraction.pdf')

    ### Activity over the whole simulation
    if data.__contains__('activity'):
        print 'plot activity'
        figure()
        plot(data.activity[0, :], 'k')
        xlabel('Step'); ylabel('activity')
        tight_layout()
        utils.saveplot('Activity.pdf')

    if data.__contains__('activityInh'):
        print 'plot activityInh'
        figure()
        plot(data.activityInh[0, :], 'k')
        xlabel('Step'); ylabel('inhibitory activity')
        tight_layout()
        utils.saveplot('ActivityInh.pdf')

    ### Rasters of the last 'raster_steps' steps only
    if data.__contains__('Spikes'):
        print 'plot spikes (last %d steps)' % last_steps
        activity = None
        if data.__contains__('activity'):
            activity = data.activity[0, -last_steps:]
        _raster(data.Spikes[0, :, -last_steps:], activity,
                'Excitatory Neuron', 'activity', 'Raster_end.pdf')

    if data.__contains__('SpikesInh'):
        print 'plot spikesInh (last %d steps)' % last_steps
        activity = None
        if data.__contains__('activityInh'):
            activity = data.activityInh[0, -last_steps:]
        _raster(data.SpikesInh[0, :, -last_steps:], activity,
                'Inhibitory Neuron', 'inhibitory activity',
                'Raster_end_inh.pdf')

    ### Final connectivity matrices
    if data.__contains__('endweight'):
        print 'plot endweight'
        figure()
        imshow(data.endweight[0], interpolation='none', aspect='auto')
        colorbar(); xlabel('Presynaptic E neuron')
        ylabel('Postsynaptic E neuron'); title('$W_{EE}$ (final)')
        tight_layout()
        utils.saveplot('FinalWeightMatrix.pdf')

    if data.__contains__('FullEndWeight'):
        print 'plot full endweight'
        figure()
        imshow(data.FullEndWeight[0], interpolation='none', aspect='auto')
        colorbar(); xlabel('Presynaptic neuron (E then I)')
        ylabel('Postsynaptic neuron (E then I)')
        title('$W_{EE}$, $W_{EI}$, $W_{IE}$ (final)')
        tight_layout()
        utils.saveplot('FinalWeightMatrix_full.pdf')

    h5.close()
