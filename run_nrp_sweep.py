"""
Launches the h_ip sweep as NRP Kubernetes Jobs, packing BATCH_SIZE
simulations into each pod so total pod count stays well under quota.

Run this from the repo root: python run_nrp_sweep.py
"""

import json
import os
import subprocess
import time
from datetime import datetime
DATE_STR = datetime.now().strftime('%m_%d_%y')

NAMESPACE = 'hengenlab'
IMAGE = 'seacore219/sorn:latest'
PVC_NAME = 'charlesd-sorn-sweep-storage'
JOB_PREFIX = 'charlesd-sweep-batch-'

H_IP_VALUES = [round(0.01 + 0.01 * i, 2) for i in range(30)]   # 30 values
N_REPEATS = 14                                                  # 420 total sims

BATCH_SIZE = 21          # sims per pod -- 420 / 21 = 20 pods exactly
CPU_PER_SIM = 1          # matches observed ~99% CPU utilization per sim
MEMORY_PER_SIM_GI = 2.0  # measured peak ~1.55GB; real margin above it

MAX_CONCURRENT_PODS = 1   # start low -- see note below before raising
POLL_SECONDS = 60

JOB_DIR = 'nrp_jobs'
if not os.path.exists(JOB_DIR):
    os.makedirs(JOB_DIR)

JOB_TEMPLATE = """apiVersion: batch/v1
kind: Job
metadata:
  name: {job_name}
  namespace: {namespace}
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: sorn
          image: {image}
          imagePullPolicy: Always
          env:
            - name: PARAM_MODULE
              value: delpapa.param_FrozenPlasticity
            - name: SORN_BATCH
              value: '{batch_json}'
          resources:
            requests:
              cpu: "{cpu}"
              memory: "{memory}Gi"
              ephemeral-storage: "4Gi"
            limits:
              cpu: "{cpu}"
              memory: "{memory}Gi"
              ephemeral-storage: "4Gi"
          volumeMounts:
            - name: sorn-storage
              mountPath: /sorn/backup
      volumes:
        - name: sorn-storage
          persistentVolumeClaim:
            claimName: {pvc_name}
"""


def build_run_list():
    runs = []
    for h_ip in H_IP_VALUES:
        for run_number in range(1, N_REPEATS + 1):
            run_tag = '%s_h_ip_%s/h_ip_%s_run%d' % (DATE_STR, h_ip, h_ip, run_number)
            runs.append({'h_ip': h_ip, 'run_tag': run_tag})
    return runs


def chunk(lst, size):
    return [lst[i:i + size] for i in range(0, len(lst), size)]


def count_active_jobs():
    out = subprocess.check_output(['kubectl', 'get', 'jobs', '-n', NAMESPACE, '-o', 'json'])
    data = json.loads(out)
    active = 0
    for item in data.get('items', []):
        if not item['metadata']['name'].startswith(JOB_PREFIX):
            continue
        status = item.get('status', {})
        if status.get('succeeded', 0) < 1 and status.get('failed', 0) < 1:
            active += 1
    return active


def submit_batch(batch_index, batch):
    job_name = '%s%03d' % (JOB_PREFIX, batch_index)

    yaml_content = JOB_TEMPLATE.format(
        job_name=job_name, namespace=NAMESPACE, image=IMAGE,
        batch_json=json.dumps(batch),
        cpu=BATCH_SIZE * CPU_PER_SIM,
        memory=BATCH_SIZE * MEMORY_PER_SIM_GI,
        pvc_name=PVC_NAME
    )
    yaml_path = os.path.join(JOB_DIR, job_name + '.yaml')
    with open(yaml_path, 'w') as f:
        f.write(yaml_content)

    subprocess.call(['kubectl', 'delete', '-f', yaml_path, '--ignore-not-found=true'])
    result = subprocess.call(['kubectl', 'apply', '-f', yaml_path])
    if result == 0:
        print('Submitted %s (%d sims)' % (job_name, len(batch)))
    else:
        print('FAILED to submit %s -- kubectl exit code %d' % (job_name, result))


def main():
    runs = build_run_list()
    batches = chunk(runs, BATCH_SIZE)
    pending = list(enumerate(batches))
    print('Total sims: %d, batched into %d pods of up to %d each' %
          (len(runs), len(batches), BATCH_SIZE))

    while pending:
        active = count_active_jobs()
        slots = MAX_CONCURRENT_PODS - active
        if slots > 0:
            to_submit = pending[:slots]
            pending = pending[slots:]
            for batch_index, batch in to_submit:
                submit_batch(batch_index, batch)
            print('%d pods queued, ~%d now active' % (len(pending), active + len(to_submit)))
        else:
            print('At concurrency limit (%d active pods) -- waiting...' % active)
        time.sleep(POLL_SECONDS)

    print('All batches submitted.')


if __name__ == '__main__':
    main()