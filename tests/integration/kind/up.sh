#!/usr/bin/env bash
# Create (or reuse) the disposable kind cluster, build the real factory runner
# image on the public UBI 9 minimal base, load it into the cluster, and deploy
# Tekton plus the factory objects. Host requirements: Podman (default) or
# Docker, kind at the version in tools/versions.lock.yaml, kubectl, git, and
# Python 3.11+ with this repository installed (pip install -e '.[dev]').
#
# Existing cluster mode: set FACTORY_HARNESS_KUBECONFIG to a kubeconfig for a
# cluster you already run (for example Rancher Desktop's k3s, Kubernetes 1.33+
# with the containerd engine and kubelet userNamespaces.idsPerPod >= 262144).
# kind is not used and no second container engine is needed: the runner image
# is built straight into the cluster's containerd with
# FACTORY_HARNESS_BUILD_COMMAND (default: nerdctl --namespace k8s.io build).
# down.sh refuses to remove such a cluster.
#
# FACTORY_CA_BUNDLE (optional, both modes): PEM file with extra CA certificates
# the runner and the images it builds must trust, e.g. a TLS-inspecting proxy's
# root certificate. Passed to toolchain/build-dist.sh and to the build stage.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/kind-review}
cluster=${FACTORY_HARNESS_CLUSTER:-factory-review}
harness=tests/integration/kind
existing=${FACTORY_HARNESS_KUBECONFIG:-}
export KIND_EXPERIMENTAL_PROVIDER=${KIND_EXPERIMENTAL_PROVIDER:-podman}
provider=${KIND_EXPERIMENTAL_PROVIDER}
rootful=${FACTORY_HARNESS_ROOTFUL:-false}
if [[ -n ${existing} ]]; then
  [[ ${rootful} == false ]] || { echo 'FACTORY_HARNESS_ROOTFUL applies to kind clusters only' >&2; exit 2; }
  [[ -s ${existing} ]] || { echo "FACTORY_HARNESS_KUBECONFIG ${existing} is not readable" >&2; exit 2; }
fi
case "${rootful}" in true|false) ;; *) echo 'FACTORY_HARNESS_ROOTFUL must be true or false' >&2; exit 2 ;; esac
runtime=("${provider}")
if [[ ${rootful} == true ]]; then
  [[ ${provider} == podman && $(id -u) != 0 ]] || { echo 'Rootful mode requires Podman and invocation by your normal user.' >&2; exit 2; }
  sudo -n true || { echo 'Run sudo -v in your terminal, then rerun bootstrap.' >&2; exit 2; }
  runtime=(sudo -n podman)
fi
[[ ${provider} == podman || ${provider} == docker ]] || { echo "Provider must be podman or docker" >&2; exit 2; }
required=(kubectl git python3)
if [[ -n ${existing} ]]; then
  # shellcheck disable=SC2206  # the build command is deliberately word-split
  build_command=(${FACTORY_HARNESS_BUILD_COMMAND:-nerdctl --namespace k8s.io build})
  required+=("${build_command[0]}")
else
  required+=(kind "${provider}")
fi
for tool in "${required[@]}"; do
  command -v "${tool}" >/dev/null || { echo "Missing ${tool}" >&2; exit 2; }
done
python3 -c 'import yaml, factory' 2>/dev/null || { echo "Install the repository first: pip install -e '.[dev]'" >&2; exit 2; }

lock_version() {
  python3 -c 'import sys, yaml; print(str(yaml.safe_load(open("tools/versions.lock.yaml"))["tools"][sys.argv[1]]["version"]).lstrip("v"))' "$1"
}
if [[ -z ${existing} ]]; then
  kind_pinned=$(lock_version kind)
  kind_actual=$(kind version -q)
  [[ ${kind_actual#v} == "${kind_pinned}" ]] || {
    echo "kind ${kind_actual} found; tools/versions.lock.yaml pins ${kind_pinned}" >&2
    exit 2
  }
fi
if [[ -z ${existing} && ${provider} == podman && ${rootful} == false ]]; then
  # Reserve the node's own IDs plus 110 pod namespaces of 262144 IDs. Check
  # before creating a cluster which cannot run the required user-namespaced pod.
  "${runtime[@]}" unshare cat /proc/self/uid_map | python3 -c '
import sys
end = 0
for line in sys.stdin:
    start, _, count = map(int, line.split())
    if start != end:
        break
    end = start + count
if end < 111 * 262144:
    raise SystemExit("Rootless Podman has insufficient mapped IDs for nested Kubernetes pods. Use FACTORY_HARNESS_ROOTFUL=true with a new cluster/state, or a Docker/Moby node with pod user-namespace support.")
'
fi
mkdir -p "${state}"
state=$(cd "${state}" && pwd)
chmod 700 "${state}"
kubeconfig="${state}/kubeconfig"
export KUBECONFIG="${kubeconfig}"
k=(kubectl --kubeconfig "${kubeconfig}")
kind_command=(kind)
if [[ ${rootful} == true ]]; then
  kind_command=(sudo -n env "KUBECONFIG=${kubeconfig}" KIND_EXPERIMENTAL_PROVIDER=podman "$(command -v kind)")
fi
if [[ -n ${existing} ]]; then
  cp "${existing}" "${kubeconfig}"
  chmod 600 "${kubeconfig}"
  cluster=$("${k[@]}" config current-context)
fi
python3 - "${state}" "${cluster}" "${provider}" "${rootful}" "${existing:+existing}" <<'PYTHON'
import json, pathlib, sys
state, cluster, provider = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
settings = dict(cluster=cluster, provider=provider, rootful=sys.argv[4] == "true",
                existing=sys.argv[5] == "existing")
path = state / 'settings.json'
previous = json.loads(path.read_text()) if path.exists() else settings
previous.setdefault("rootful", False)
previous.setdefault("existing", False)
previous.pop("registryPort", None)
if previous != settings:
    raise SystemExit('State belongs to different settings; use another state directory')
path.write_text(json.dumps(settings) + '\n')
config = {'kind': 'Cluster', 'apiVersion': 'kind.x-k8s.io/v1alpha4',
    # The runner maps 100000:65536 for nested RootlessKit/Podman. The default
    # 65536-ID pod namespace cannot contain that range.
    'kubeadmConfigPatches': ['kind: KubeletConfiguration\nuserNamespaces:\n  idsPerPod: 262144\n'],
    'networking': {'apiServerAddress': '127.0.0.1'},
    'nodes': [{'role': 'control-plane'}]}
(state / 'cluster.json').write_text(json.dumps(config))
PYTHON
if [[ -n ${existing} ]]; then
  : # the cluster already exists; nothing to create
elif ! "${kind_command[@]}" get clusters | grep -qx "${cluster}"; then
  create=("${kind_command[@]}" create cluster --name "${cluster}" --kubeconfig "${kubeconfig}" --config "${state}/cluster.json" --wait 180s)
  if [[ ${provider} == podman && ${rootful} == false ]]; then
    systemd-run --scope --user -p Delegate=yes "${create[@]}"
  else
    "${create[@]}"
  fi
elif [[ ! -s ${kubeconfig} ]]; then
  echo "${cluster} exists without this harness kubeconfig; refusing to reuse it." >&2
  exit 2
fi
if [[ ${rootful} == true ]]; then
  sudo -n chown "$(id -u):$(id -g)" "${kubeconfig}"
  chmod 600 "${kubeconfig}"
fi
# Existing clusters do not pick up kubeadm creation patches on a rerun.
node=$("${k[@]}" get nodes -o jsonpath='{.items[0].metadata.name}')
ids_per_pod=$("${k[@]}" get --raw "/api/v1/nodes/${node}/proxy/configz" |
  python3 -c 'import json, sys; print(json.load(sys.stdin)["kubeletconfig"].get("userNamespaces", {}).get("idsPerPod", 65536))')
if (( ids_per_pod < 262144 )); then
  echo 'Harness requires kubelet userNamespaces.idsPerPod >= 262144. For kind, create a new harness cluster/state; for another cluster, add a kubelet drop-in (see docs/local-kubernetes-testing.md).' >&2
  exit 2
fi

# The real runner image, on the public UBI base. dist/ is assembled by
# toolchain/build-dist.sh (pinned tools and a wheel of this repository).
architecture=$("${k[@]}" get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}')
FACTORY_TOOLCHAIN_ARCH="${architecture}" toolchain/build-dist.sh dist
ubi_version=$(python3 -c 'import yaml; print(yaml.safe_load(open("catalog/images/ubi9-minimal.yaml"))["product"]["version"])')
build_args=(--platform "linux/${architecture}"
  --build-arg "BASE_REF=registry.access.redhat.com/ubi9/ubi-minimal:${ubi_version}"
  -t localhost/factory-review-runner:review -f toolchain/Containerfile.factory-runner .)
if [[ -n ${existing} ]]; then
  # Built directly into the cluster's containerd image store; nothing to load.
  # Plain progress so a failed step's full output survives, also in the log.
  if ! "${build_command[@]}" --progress=plain "${build_args[@]}" 2>&1 | tee "${state}/runner-build.log"; then
    echo "runner image build failed; full log: ${state}/runner-build.log" >&2
    exit 1
  fi
else
  "${runtime[@]}" build "${build_args[@]}"
  rm -f "${state}/runner.tar"
  if [[ ${provider} == podman ]]; then
    "${runtime[@]}" save --format docker-archive -o "${state}/runner.tar" localhost/factory-review-runner:review
  else
    docker save -o "${state}/runner.tar" localhost/factory-review-runner:review
  fi
  if [[ ${rootful} == true ]]; then
    sudo -n chown "$(id -u):$(id -g)" "${state}/runner.tar"
  fi
  "${kind_command[@]}" load image-archive --name "${cluster}" "${state}/runner.tar"
fi
# Tell deploy.sh whether the build stage should mount the runner's CA bundle.
if [[ -n ${FACTORY_CA_BUNDLE:-} ]]; then
  : >"${state}/ca-bundle"
else
  rm -f "${state}/ca-bundle"
fi
FACTORY_HARNESS_STATE="${state}" "${harness}/deploy.sh"
if [[ ${rootful} == true ]]; then
  # The tested rootful kind node needs crun for user-namespaced sandbox sysfs
  # mounts. Provision and probe it once; retain the selected class in state.
  handler=''
  if [[ -s ${state}/runtime-class ]]; then
    handler=$("${k[@]}" get runtimeclass "$(cat "${state}/runtime-class")" -o jsonpath='{.handler}' 2>/dev/null || true)
  fi
  if [[ -z ${handler} ]] || ! "${k[@]}" get node "${node}" -o json |
    python3 -c 'import json, sys; node = json.load(sys.stdin); handler = sys.argv[1]
ok = any(h.get("name") == handler and h.get("features", {}).get("userNamespaces") for h in node["status"].get("runtimeHandlers", []))
raise SystemExit(0 if ok else 1)' "${handler}" >/dev/null; then
    python3 "${harness}/probe-crun.py" --state "${state}"
  fi
fi
