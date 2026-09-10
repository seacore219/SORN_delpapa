"""Run SORN simulations sequentially and file them where the avalanche scripts look.

    python run_replication.py N200 10

Runs common/test_single.py ten times with delpapa.param_Zheng2013 and moves each
result into backup/N200/1 ... backup/N200/10, giving the

    <exper_path>/<n>/common/result.h5

layout that delpapa/avalanches/Fig2.py reads.

For the other network sizes of Fig. 2D-F, change c.N_e in
delpapa/param_Zheng2013.py as the readme describes, then run again with the
matching label (N50, N100, N400, N800).
"""
from __future__ import print_function
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
COMMON = os.path.join(ROOT, 'common')
ATTEMPTS = os.path.join(ROOT, 'backup', 'test_single')
PARAM = 'delpapa.param_Zheng2013'


def subdirs(path):
    if not os.path.isdir(path):
        return set()
    return set(d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d)))


def main(label, n_runs):
    dest_root = os.path.join(ROOT, 'backup', label)
    for run in range(1, n_runs + 1):
        before = subdirs(ATTEMPTS)
        print('[%d/%d] %s' % (run, n_runs, label))
        code = subprocess.call([sys.executable, 'test_single.py', PARAM], cwd=COMMON)
        if code != 0:
            print('test_single.py exited with %d, stopping' % code)
            return 1
        new = subdirs(ATTEMPTS) - before
        if len(new) != 1:
            print('expected one new folder in %s, found %d, stopping'
                  % (ATTEMPTS, len(new)))
            return 1
        if not os.path.isdir(dest_root):
            os.makedirs(dest_root)
        dest = os.path.join(dest_root, str(run))
        shutil.move(os.path.join(ATTEMPTS, new.pop()), dest)
        print('  -> %s' % dest)
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(sys.argv[1], int(sys.argv[2])))
