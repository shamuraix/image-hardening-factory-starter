#!/usr/bin/env bash
# Remove what the harness created: the factory-harness namespace (its runs,
# workspace volumes, Tasks, and placeholder Secrets). Tekton Pipelines and the
# runner image stay, because docs/local-rancher-desktop.md uses the same Tekton
# install; remove them yourself if nothing else needs them.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/harness}
[[ -s ${state}/kubeconfig ]] || { echo "No ${state}/kubeconfig; nothing to remove" >&2; exit 2; }
kubectl --kubeconfig "${state}/kubeconfig" delete namespace factory-harness --ignore-not-found --wait=true
