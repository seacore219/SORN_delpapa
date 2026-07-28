"""
Launches the h_ip sweep as NRP Kubernetes Jobs, throttled to stay safely
under the namespace's 200-pod quota. Submits in waves, checking how many
of our jobs are still active before submitting more.

Run this from the repo root: python run_nrp_sweep.py
"""

import json
import os
import subprocess
import time

NAMESPACE = 'hengenlab'
IMAGE = 'seacore219/sorn:latest'
PVC_NAME = 'charlesd-sorn-sweep-storage'
JOB_PREFIX = 'charlesd-sweep-hip-'

H_IP_VALUES = [0.01, 0.02]   # 2 values
N_REPEATS = 10                # 10 runs each = 20 jobs total

MAX_CONCURRENT = 150   # safely under the 200-pod namespace quota
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
            - name: SORN_H_IP
              value: "{h_ip}"
            - name: SORN_RUN_TAG
              value: {run_tag}
          resources:
            requests:
              cpu: "1"
              memory: "3Gi"
              ephemeral-storage: "2Gi"
            limits:
              cpu: "1"
              memory: "3Gi"
              ephemeral-storage: "2Gi"
          volumeMounts:
            - name: sorn-storage
              mountPath: /sorn/backup
      volumes:
        - name: sorn-storage
          persistentVolumeClaim:
            claimName: {pvc_name}
"""


def job_name_for(h_ip, run_number):
    h_ip_str = str(h_ip).replace('.', 'p')
    return '%s%s-run%d' % (JOB_PREFIX, h_ip_str, run_number)


def build_job_list():
    jobs = []
    for h_ip in H_IP_VALUES:
        for run_number in range(1, N_REPEATS + 1):
            jobs.append((h_ip, run_number))
    return jobs


def count_active_jobs():
    """Counts our jobs that haven't succeeded or failed yet."""
    out = subprocess.check_output(['kubectl', 'get', 'jobs', '-n', NAMESPACE, '-o', 'json'])
    data = json.loads(out)
    active = 0
    for item in data.get('items', []):
        name = item['metadata']['name']
        if not name.startswith(JOB_PREFIX):
            continue
        status = item.get('status', {})
        if status.get('succeeded', 0) < 1 and status.get('failed', 0) < 1:
            active += 1
    return active


def submit_job(h_ip, run_number):
    run_tag = 'h_ip_%s_run%d' % (h_ip, run_number)
    job_name = job_name_for(h_ip, run_number)

    yaml_content = JOB_TEMPLATE.format(
        job_name=job_name, namespace=NAMESPACE, image=IMAGE,
        h_ip=h_ip, run_tag=run_tag, pvc_name=PVC_NAME
    )
    yaml_path = os.path.join(JOB_DIR, job_name + '.yaml')
    with open(yaml_path, 'w') as f:
        f.write(yaml_content)

    subprocess.call(['kubectl', 'delete', '-f', yaml_path, '--ignore-not-found=true'])
    subprocess.call(['kubectl', 'apply', '-f', yaml_path])
    print('Submitted %s (h_ip=%s, run=%d)' % (job_name, h_ip, run_number))


def main():
    pending = build_job_list()
    print('Total jobs to run: %d' % len(pending))

    while pending:
        active = count_active_jobs()
        slots = MAX_CONCURRENT - active
        if slots > 0:
            batch = pending[:slots]
            pending = pending[slots:]
            for h_ip, run_number in batch:
                submit_job(h_ip, run_number)
            print('%d jobs queued, ~%d now active' % (len(pending), active + len(batch)))
        else:
            print('At concurrency limit (%d active) -- waiting...' % active)
        time.sleep(POLL_SECONDS)

    print('All jobs submitted. Check `kubectl get jobs -n hengenlab` until all show Complete.')


if __name__ == '__main__':
    main()