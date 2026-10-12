#!/usr/bin/env bash
# Build the real factory runner image into an existing cluster's containerd and
# deploy Tekton plus the harness objects (deploy.sh). The cluster must meet the
# production platform floor (docs/scope.md): Kubernetes 1.28 or later with the
# containerd runtime. Rancher Desktop with the containerd engine is the
# documented setup (docs/local-harness.md).
#
# Env:
#   FACTORY_HARNESS_KUBECONFIG     kubeconfig for the cluster (required). It is
#                                  copied into the state directory, so the
#                                  harness never follows your shell's current
#                                  context to another cluster.
#   FACTORY_HARNESS_STATE          state directory (default .local-factory/harness)
#   FACTORY_HARNESS_BUILD_COMMAND  build command that writes into the node's
#                                  containerd image store
#                                  (default: nerdctl --namespace k8s.io build)
#   FACTORY_CA_BUNDLE              optional PEM with extra CA certificates, e.g.
#                                  a TLS-inspecting proxy's root
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/harness}
harness=tests/integration/local
source_kubeconfig=${FACTORY_HARNESS_KUBECONFIG:?Set FACTORY_HARNESS_KUBECONFIG to the cluster kubeconfig (Rancher Desktop: ~/.kube/config)}
[[ -s ${source_kubeconfig} ]] || { echo "FACTORY_HARNESS_KUBECONFIG ${source_kubeconfig} is not readable" >&2; exit 2; }
# shellcheck disable=SC2206  # the build command is deliberately word-split
build_command=(${FACTORY_HARNESS_BUILD_COMMAND:-nerdctl --namespace k8s.io build})
for tool in kubectl git python3 "${build_command[0]}"; do
  command -v "${tool}" >/dev/null || { echo "Missing ${tool}" >&2; exit 2; }
done
python3 -c 'import yaml, factory' 2>/dev/null || { echo "Install the repository first: pip install -e '.[dev]'" >&2; exit 2; }

mkdir -p "${state}"
state=$(cd "${state}" && pwd)
chmod 700 "${state}"
kubeconfig="${state}/kubeconfig"
cp "${source_kubeconfig}" "${kubeconfig}"
chmod 600 "${kubeconfig}"
k=(kubectl --kubeconfig "${kubeconfig}")
context=$("${k[@]}" config current-context)
if [[ -s ${state}/context && $(cat "${state}/context") != "${context}" ]]; then
  echo "State ${state} belongs to context $(cat "${state}/context"), not ${context}; use another FACTORY_HARNESS_STATE" >&2
  exit 2
fi
printf '%s\n' "${context}" >"${state}/context"

# The same floor production has: Kubernetes 1.28+, containerd.
"${k[@]}" version -o json | python3 -c '
import json, re, sys
server = json.load(sys.stdin)["serverVersion"]
minor = int(re.match(r"\d+", server["minor"]).group())
if (int(server["major"]), minor) < (1, 28):
    raise SystemExit(f"Kubernetes {server[\"gitVersion\"]} is below the factory floor of 1.28 (docs/scope.md)")
'
node=$("${k[@]}" get nodes -o jsonpath='{.items[0].metadata.name}')
runtime=$("${k[@]}" get node "${node}" -o jsonpath='{.status.nodeInfo.containerRuntimeVersion}')
[[ ${runtime} == containerd://* ]] || {
  echo "Node ${node} runs ${runtime}; the harness builds the runner into containerd (Rancher Desktop: Preferences > Container Engine > containerd)" >&2
  exit 2
}

# The real runner image, for the node's architecture, on the public UBI 9
# base. dist/ is assembled by toolchain/build-dist.sh; the Containerfile stops
# if dist/ was assembled for another architecture.
architecture=$("${k[@]}" get node "${node}" -o jsonpath='{.status.nodeInfo.architecture}')
FACTORY_TOOLCHAIN_ARCH="${architecture}" toolchain/build-dist.sh dist
ubi_version=$(python3 -c 'import yaml; print(yaml.safe_load(open("catalog/images/ubi9-minimal.yaml"))["product"]["version"])')
if ! "${build_command[@]}" --progress=plain --platform "linux/${architecture}" \
  --build-arg "BASE_REF=registry.access.redhat.com/ubi9/ubi-minimal:${ubi_version}" \
  -t localhost/factory-harness-runner:local -f toolchain/Containerfile.factory-runner . \
  2>&1 | tee "${state}/runner-build.log"; then
  echo "runner image build failed; full log: ${state}/runner-build.log" >&2
  exit 1
fi
# Tell deploy.sh whether builds should mount the runner's CA bundle.
if [[ -n ${FACTORY_CA_BUNDLE:-} ]]; then
  : >"${state}/ca-bundle"
else
  rm -f "${state}/ca-bundle"
fi
FACTORY_HARNESS_STATE="${state}" "${harness}/deploy.sh"
