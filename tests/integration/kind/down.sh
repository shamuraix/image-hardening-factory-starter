#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/kind-review}
[[ -s ${state}/kubeconfig && -s ${state}/settings.json ]] || {
  echo 'No harness kubeconfig/settings; refusing to delete a cluster.' >&2; exit 2;
}
cluster=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["cluster"])' "${state}/settings.json")
provider=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["provider"])' "${state}/settings.json")
export KUBECONFIG="$(cd "${state}" && pwd)/kubeconfig"
context=$(kubectl config current-context)
[[ ${context} == "kind-${cluster}" ]] || { echo 'Harness context mismatch; refusing deletion' >&2; exit 2; }
rootful=$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1])).get("rootful", False)).lower())' "${state}/settings.json")
if [[ ${rootful} == true ]]; then
  sudo -n env "KUBECONFIG=${KUBECONFIG}" KIND_EXPERIMENTAL_PROVIDER=podman "$(command -v kind)" delete cluster --name "${cluster}"
else
  KIND_EXPERIMENTAL_PROVIDER="${provider}" kind delete cluster --name "${cluster}"
fi
