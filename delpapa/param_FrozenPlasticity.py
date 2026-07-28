from __future__ import division
import numpy as np
import utils
utils.backup(__file__)
from common.defaults import *
import os

c.N_e = 200
c.N_i = int(np.floor(0.2*c.N_e))
c.N = c.N_e + c.N_i
c.N_u_e = 0

c.W_ee = utils.Bunch(use_sparse=True,
                     lamb=0.1*c.N_e,
                     avoid_self_connections=True,
                     eta_stdp = 0.004,
                     sp_prob =  c.N_e*(c.N_e-1)*(0.1/(200*199)),
                     sp_initial = 0.001,
                     no_prune = False,
                     upper_bound = 1
                     )

c.W_ei = utils.Bunch(use_sparse=False,
                     lamb=0.2*c.N_e,
                     avoid_self_connections=True,
                     eta_istdp = 0.001,
                     h_ip=0.1)

c.W_ie = utils.Bunch(use_sparse=False,
                     lamb=1.0*c.N_i,
                     avoid_self_connections=True)

c.steps_plastic = 6000000 # steps before the perturbation
c.steps_perturbation = 250000 # steps after the perturbation
c.N_steps = (c.steps_plastic + 2*c.steps_perturbation)
c.N_iterations = 1
c.eta_ip = 0.01
c.h_ip = float(os.environ.get('SORN_H_IP', 0.1))
c.noise_sig = np.sqrt(0.05)
c.noise_fire = 0
c.noise_fire_struc = 0

c.stats.file_suffix = 'test'
c.display = True
# save the spikes of the perturbation perdiod:
# first half is the non-perturbated network
# second half is the perturbated network
c.stats.only_last_spikes = (2*c.steps_perturbation)
c.stats.save_spikes = True
c.stats.only_last_spikes = 6000000

c.experiment.module = 'delpapa.experiment_FrozenPlasticity'
c.experiment.name = 'Experiment_test'
