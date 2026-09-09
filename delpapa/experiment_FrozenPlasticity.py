from __future__ import division
from pylab import *
import utils
utils.backup(__file__)

#~ from delpapa.plot import plot_results_perturbation as plot_results_single
from delpapa.plot_standard import plot_results_standard as plot_results_single

from common.sources import NoSource
from common.experiments import AbstractExperiment
from common.sorn_stats import *

class Experiment_test(AbstractExperiment):
    def start(self):
        super(Experiment_test,self).start()
        c = self.params.c

        self.inputsource = NoSource()

        stats_single = [
                         ActivityStat(),
                         ActivityInhibStat(),
                         SpikesStat(),          # E spikes -> 'Spikes'
                         SpikesInhStat(),       # I spikes -> 'SpikesInh'
                         ConnectionFractionStat(),
                         EndWeightStat(),       # W_ee -> 'endweight'
                         FullEndWeightStat(),   # W_ee, W_ei, W_ie -> 'FullEndWeight'
                        ]
        return (self.inputsource,stats_single)

    def reset(self,sorn):
        super(Experiment_test,self).reset(sorn)
        c = self.params.c
        stats = sorn.stats # init sets sorn.stats to None
        sorn.__init__(c,self.inputsource)
        sorn.stats = stats

    def run(self,sorn):
        super(Experiment_test,self).run(sorn)
        c = self.params.c


        # PERTURBATION CURRENTLY DISABLED - the three simulation calls below
        # run as one continuous, fully plastic simulation of c.N_steps steps.
        # To restore the frozen plasticity protocol of Del Papa et al. (2017),
        # uncomment every '#~' line in this method (and nothing else).

        # Initial pahse with plsaticity
        print '\n\nInitial Phase:'
        sorn.simulation(c.steps_plastic)
        #~ newseed = randint(999999) # random seed
        tmpstats = sorn.stats
        sorn.stats = 0
        filename = utils.logfilename("net_before_pert.pickle")
        sorn.quicksave(filename)
        sorn.stats = tmpstats

        # Reset point: next line runs the 'normal' SORN
        #~ print '\n\nNon-frozen steps:'
        print '\n\nSecond phase (continues the same run):'
        #~ seed(newseed)
        sorn.simulation(c.steps_perturbation)


        # Return to the reset point, freezes plasticity and run again
        #~ print '\n\nFrozen steps:'
        print '\n\nThird phase (continues the same run):'
        #~ sorn = sorn.quickload(filename)
        #~ sorn.stats = tmpstats
        #~ sorn.stats.obj = sorn

        # Freeze plasticity
        # comment a line NOT to freeze a specific plasticity mechanism
        #~ sorn.W_ee.c.eta_stdp = 0     # freezes STDP
        #~ sorn.W_ei.c.eta_istdp = 0    # freezes iSTDP
        #~ sorn.W_ee.c.sp_prob = 0      # freezes SP
        #~ c.eta_ip = 0                 # freezes IP
        #~ c.noise_sig = 0              # freezes noise

        #~ seed(newseed)
        sorn.simulation(c.steps_perturbation)

        return {'source_plastic':self.inputsource}

    def plot_single(self,path,filename):
        plot_results_single(path,filename)
