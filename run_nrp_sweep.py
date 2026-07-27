"""
Launches the h_ip sweep as NRP Kubernetes Jobs, one per (h_ip, run_number).

Unlike the local sweep script, we don't manage concurrency ourselves --
kubectl apply just submits each Job to the cluster and returns immediately,
and NRP's own scheduler handles running them.

Run this from the repo root: python run_nrp_sweep.py
"""

import os
import subprocess

NAMESPACE = 'hengenlab'
IMAGE = 'seacore219/sorn:latest'
PVC_NAME = 'charlesd-sorn-sweep-storage'

H_IP_VALUES = [round(0.05 + 0.05 * i, 2) for i in range(6)]  # 0.05 ... 0.30
N_REPEATS = 3

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
              memory: "2Gi"
              ephemeral-storage: "2Gi"
            limits:
              cpu: "1"
              memory: "2Gi"
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
    # Kubernetes job names can't contain dots, so 0.05 -> 0p05
    h_ip_str = str(h_ip).replace('.', 'p')
    return 'charlesd-sweep-hip-%s-run%d' % (h_ip_str, run_number)


def main():
    for h_ip in H_IP_VALUES:
        for run_number in range(1, N_REPEATS + 1):
            run_tag = 'h_ip_%s_run%d' % (h_ip, run_number)
            job_name = job_name_for(h_ip, run_number)

            yaml_content = JOB_TEMPLATE.format(
                job_name=job_name,
                namespace=NAMESPACE,
                image=IMAGE,
                h_ip=h_ip,
                run_tag=run_tag,
                pvc_name=PVC_NAME
            )

            yaml_path = os.path.join(JOB_DIR, job_name + '.yaml')
            with open(yaml_path, 'w') as f:
                f.write(yaml_content)

            # Always delete any old version first -- Job specs are immutable
            subprocess.call(['kubectl', 'delete', '-f', yaml_path, '--ignore-not-found=true'])
            subprocess.call(['kubectl', 'apply', '-f', yaml_path])

            print('Submitted %s (h_ip=%s, run=%d)' % (job_name, h_ip, run_number))


if __name__ == '__main__':
    main()