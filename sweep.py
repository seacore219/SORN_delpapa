"""
Runs the FrozenPlasticity experiment once per value of h_ip,
by setting an environment variable that param_FrozenPlasticity.py
reads before each run.

Run this from the repo root: python sweep_h_ip.py
"""

import subprocess
import os

# One log file per run, so you can see exactly what happened
# for each h_ip value, including which backup/ folder it wrote to.
LOG_DIR = 'sweep_logs'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

h_ip_values = [round(0.05 + 0.05 * i, 2) for i in range(6)]  # 0.05, 0.10, ..., 0.30

for h_ip in h_ip_values:
    print('\n=== Running with h_ip = %s ===' % h_ip)

    env = os.environ.copy()
    env['SORN_H_IP'] = str(h_ip)

    log_path = os.path.join(LOG_DIR, 'h_ip_%s.log' % h_ip)
    with open(log_path, 'w') as log_file:
        subprocess.call(
            ['python', 'test_single.py', 'delpapa.param_FrozenPlasticity'],
            cwd='common',
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT
        )

    print('Done. Log saved to %s' % log_path)