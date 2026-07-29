"""
Runs a batch of simulations in parallel inside a single pod, each as its
own subprocess. Reads its work list from the SORN_BATCH environment
variable, a JSON string like:
  [{"h_ip": 0.01, "run_tag": "h_ip_0.01_run1"}, ...]

Each simulation's output (including the /usr/bin/time -v summary) goes
to its own log file under /sorn/backup/logs/<run_tag>.log.
"""

import json
import os
import subprocess
import sys


def ensure_dir(path):
    """Create a directory, tolerating a race with another concurrent pod."""
    try:
        os.makedirs(path)
    except OSError:
        if not os.path.isdir(path):
            raise


def main():
    batch_json = os.environ.get('SORN_BATCH')
    if not batch_json:
        print('SORN_BATCH environment variable not set -- nothing to run')
        sys.exit(1)

    batch = json.loads(batch_json)
    param_module = os.environ.get('PARAM_MODULE', 'delpapa.param_FrozenPlasticity')

    log_dir = '/sorn/backup/logs'
    ensure_dir(log_dir)

    procs = []
    for entry in batch:
        h_ip = entry['h_ip']
        run_tag = entry['run_tag']

        env = os.environ.copy()
        env['SORN_H_IP'] = str(h_ip)
        env['SORN_RUN_TAG'] = run_tag

        log_path = os.path.join(log_dir, run_tag + '.log')
        ensure_dir(os.path.dirname(log_path))
        log_file = open(log_path, 'w')

        print('Launching %s (h_ip=%s)' % (run_tag, h_ip))
        proc = subprocess.Popen(
            ['/usr/bin/time', '-v', 'python', '-u', 'test_single.py', param_module],
            cwd='common',
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT
        )
        procs.append((proc, log_file, run_tag))

    failures = []
    for proc, log_file, run_tag in procs:
        ret = proc.wait()
        log_file.close()
        status = 'OK' if ret == 0 else 'FAILED (exit %d)' % ret
        print('%s: %s' % (run_tag, status))
        if ret != 0:
            failures.append(run_tag)

    if failures:
        print('%d of %d runs failed: %s' % (len(failures), len(batch), ', '.join(failures)))
        sys.exit(1)
    print('All %d runs in this pod completed successfully.' % len(batch))


if __name__ == '__main__':
    main()