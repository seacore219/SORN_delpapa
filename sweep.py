"""
Sweeps h_ip over a range of values, running N_REPEATS repetitions of each,
with up to MAX_PARALLEL runs executing at the same time.

Each run is a fully separate subprocess (important for this codebase --
see earlier discussion about global state in utils/backup.py). Each gets
a unique SORN_RUN_TAG so their backup/ folders never collide, even when
launched simultaneously.

Run this from the repo root: python sweep_h_ip.py
"""

import subprocess
import os
import time

LOG_DIR = 'sweep_logs'
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

H_IP_VALUES = [round(0.05 + 0.05 * i, 2) for i in range(6)]  # 0.05 ... 0.30
N_REPEATS = 3       # how many times to repeat each h_ip value
MAX_PARALLEL = 3    # how many runs allowed to run at once


def build_jobs():
    jobs = []
    for h_ip in H_IP_VALUES:
        for run_number in range(1, N_REPEATS + 1):
            run_tag = 'h_ip_%s_run%d' % (h_ip, run_number)
            jobs.append((h_ip, run_tag))
    return jobs


def launch(h_ip, run_tag):
    env = os.environ.copy()
    env['SORN_H_IP'] = str(h_ip)
    env['SORN_RUN_TAG'] = run_tag

    log_path = os.path.join(LOG_DIR, run_tag + '.log')
    log_file = open(log_path, 'w')

    proc = subprocess.Popen(
        ['python', 'test_single.py', 'delpapa.param_FrozenPlasticity'],
        cwd='common',
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT
    )
    return proc, log_file


def main():
    jobs = build_jobs()
    running = []  # list of (proc, log_file, run_tag)

    while jobs or running:
        while jobs and len(running) < MAX_PARALLEL:
            h_ip, run_tag = jobs.pop(0)
            print('Starting %s (h_ip=%s)' % (run_tag, h_ip))
            proc, log_file = launch(h_ip, run_tag)
            running.append((proc, log_file, run_tag))

        still_running = []
        for proc, log_file, run_tag in running:
            if proc.poll() is None:
                still_running.append((proc, log_file, run_tag))
            else:
                log_file.close()
                print('Finished %s (exit code %d)' % (run_tag, proc.returncode))
        running = still_running

        time.sleep(1)

    print('\nAll runs complete. Logs in %s/, data in backup/' % LOG_DIR)


if __name__ == '__main__':
    main()