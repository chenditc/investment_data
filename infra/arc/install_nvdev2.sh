#!/usr/bin/env bash
set -euo pipefail

ARC_VERSION="${ARC_VERSION:-0.14.2}"
CONTROLLER_NS="${CONTROLLER_NS:-arc-systems}"
RUNNER_NS="${RUNNER_NS:-arc-runners}"
RUNNER_RELEASE="${RUNNER_RELEASE:-investment-arc}"
RUNNER_VALUES="${RUNNER_VALUES:-infra/arc/investment-runner-scale-set.values.yaml}"
RUNNER_PVC="${RUNNER_PVC:-infra/arc/investment-data-docker-graph.pvc.yaml}"
GITHUB_PAT="${GITHUB_PAT:-}"

if [[ -z "${GITHUB_PAT}" ]]; then
  echo "GITHUB_PAT must be set." >&2
  exit 1
fi

helm upgrade --install arc \
  --namespace "${CONTROLLER_NS}" \
  --create-namespace \
  --version "${ARC_VERSION}" \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller

kubectl create namespace "${RUNNER_NS}" --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic investment-data-arc-github \
  --namespace "${RUNNER_NS}" \
  --from-literal=github_token="${GITHUB_PAT}" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "${RUNNER_PVC}"

helm upgrade --install "${RUNNER_RELEASE}" \
  --namespace "${RUNNER_NS}" \
  --create-namespace \
  --version "${ARC_VERSION}" \
  -f "${RUNNER_VALUES}" \
  oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set

kubectl get pods -n "${CONTROLLER_NS}"
kubectl get pods -n "${RUNNER_NS}"
