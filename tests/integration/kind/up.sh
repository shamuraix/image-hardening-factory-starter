#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/kind-review}
cluster=${FACTORY_HARNESS_CLUSTER:-factory-review}
harness=tests/integration/kind
export KIND_EXPERIMENTAL_PROVIDER=${KIND_EXPERIMENTAL_PROVIDER:-podman}
provider=${KIND_EXPERIMENTAL_PROVIDER}
rootful=${FACTORY_HARNESS_ROOTFUL:-false}
case "${rootful}" in true|false) ;; *) echo 'FACTORY_HARNESS_ROOTFUL must be true or false' >&2; exit 2 ;; esac
runtime=("${provider}")
if [[ ${rootful} == true ]]; then
  [[ ${provider} == podman && $(id -u) != 0 ]] || { echo 'Rootful mode requires Podman and invocation by your normal user.' >&2; exit 2; }
  sudo -n true || { echo 'Run sudo -v in your terminal, then rerun bootstrap.' >&2; exit 2; }
  runtime=(sudo -n podman)
fi
[[ ${provider} == podman || ${provider} == docker ]] || { echo "Provider must be podman or docker" >&2; exit 2; }
for tool in kind "${provider}" kubectl skopeo curl openssl python3 jq; do
  command -v "${tool}" >/dev/null || { echo "Missing ${tool}" >&2; exit 2; }
done
if [[ ${provider} == podman && ${rootful} == false ]]; then
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
mkdir -p "${state}"/{tls,client-ca,runner/tools}
chmod 700 "${state}"
kubeconfig="${state}/kubeconfig"
export KUBECONFIG="${kubeconfig}"
k=(kubectl --kubeconfig "${kubeconfig}")
kind_command=(kind)
if [[ ${rootful} == true ]]; then
  kind_command=(sudo -n env "KUBECONFIG=${kubeconfig}" KIND_EXPERIMENTAL_PROVIDER=podman "$(command -v kind)")
fi
python3 - "${state}" "${cluster}" "${provider}" "${rootful}" <<'PYTHON'
import json, os, pathlib, sys
state, cluster, provider = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
settings = dict(cluster=cluster, provider=provider, rootful=sys.argv[4] == "true",
    jenkinsPort=int(os.environ.get('FACTORY_HARNESS_JENKINS_PORT', '18080')),
    registryPort=int(os.environ.get('FACTORY_HARNESS_REGISTRY_PORT', '15443')))
path = state / 'settings.json'
previous = json.loads(path.read_text()) if path.exists() else settings
previous.setdefault("rootful", False)
if previous != settings:
    raise SystemExit('State belongs to different settings; use another state directory')
path.write_text(json.dumps(settings) + '\n')
config = {'kind':'Cluster', 'apiVersion':'kind.x-k8s.io/v1alpha4',
    # The runner maps 100000:65536 for nested RootlessKit/Podman. The default
    # 65536-ID pod namespace cannot contain that range.
    'kubeadmConfigPatches': ['kind: KubeletConfiguration\nuserNamespaces:\n  idsPerPod: 262144\n'],
    'networking':{'apiServerAddress':'127.0.0.1'}, 'nodes':[{'role':'control-plane',
    'extraPortMappings':[{'containerPort':30080, 'hostPort':settings['jenkinsPort'], 'listenAddress':'127.0.0.1'},
                         {'containerPort':30500, 'hostPort':settings['registryPort'], 'listenAddress':'127.0.0.1'}]}]}
(state / 'cluster.json').write_text(json.dumps(config))
PYTHON
if ! "${kind_command[@]}" get clusters | grep -qx "${cluster}"; then
  create=("${kind_command[@]}" create cluster --name "${cluster}" --kubeconfig "${kubeconfig}" --config "${state}/cluster.json" --wait 180s)
  if [[ ${provider} == podman && ${rootful} == false ]]; then
    systemd-run --scope --user -p Delegate=yes "${create[@]}"
  else
    "${create[@]}"
  fi
elif [[ ! -s ${kubeconfig} ]]; then
  echo 'factory-review exists without this harness kubeconfig; refusing to reuse it.' >&2
  exit 2
fi
if [[ ${rootful} == true ]]; then
  sudo -n chown "$(id -u):$(id -g)" "${kubeconfig}"
  chmod 600 "${kubeconfig}"
fi
# Existing clusters do not pick up kubeadm creation patches on a rerun.
ids_per_pod=$("${k[@]}" get --raw "/api/v1/nodes/${cluster}-control-plane/proxy/configz" | jq -r '.kubeletconfig.userNamespaces.idsPerPod // 65536')
if (( ids_per_pod < 262144 )); then
  echo 'Harness requires kubelet userNamespaces.idsPerPod >= 262144. Create a new harness cluster/state; existing clusters are not reconfigured automatically.' >&2
  exit 2
fi
if [[ ! -s ${state}/tls/ca.crt ]] || ! openssl x509 -checkend 3600 -noout -in "${state}/tls/ca.crt"; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 7 \
    -keyout "${state}/tls/tls.key" -out "${state}/tls/ca.crt" \
    -subj /CN=factory-review-registry \
    -addext 'subjectAltName=DNS:registry.factory-harness.svc.cluster.local,DNS:localhost,IP:127.0.0.1'
  chmod 600 "${state}/tls/tls.key"
fi
cp "${state}/tls/ca.crt" "${state}/client-ca/ca.crt"
cp "${state}/tls/ca.crt" "${state}/runner/ca.crt"
cp "${harness}/configure-uidmap.py" "${state}/runner/configure-uidmap.py"
architecture=$("${k[@]}" get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}')
"${harness}/download-tools.sh" "${state}/runner/tools" "${architecture}"
"${runtime[@]}" build -t localhost/factory-review-jenkins:review -f "${harness}/Containerfile.jenkins" "${harness}"
subuid_start=100000
subuid_count=65536
"${runtime[@]}" build --build-arg "FACTORY_SUBUID_START=${subuid_start}" --build-arg "FACTORY_SUBUID_COUNT=${subuid_count}" -t localhost/factory-review-runner:review -f "${harness}/Containerfile.runner" "${state}/runner"
for image in jenkins runner; do
  rm -f "${state}/${image}.tar"
  if [[ ${provider} == podman ]]; then
    "${runtime[@]}" save --format docker-archive -o "${state}/${image}.tar" "localhost/factory-review-${image}:review"
  else
    docker save -o "${state}/${image}.tar" "localhost/factory-review-${image}:review"
  fi
  if [[ ${rootful} == true ]]; then
    sudo -n chown "$(id -u):$(id -g)" "${state}/${image}.tar"
  fi
  "${kind_command[@]}" load image-archive --name "${cluster}" "${state}/${image}.tar"
done
FACTORY_HARNESS_STATE="${state}" "${harness}/deploy.sh"
if [[ ${rootful} == true ]]; then
  # The tested rootful kind node needs crun for user-namespaced sandbox sysfs
  # mounts. Provision and probe it once; retain the selected class in state.
  handler=''
  if [[ -s ${state}/runtime-class ]]; then
    handler=$("${k[@]}" get runtimeclass "$(cat "${state}/runtime-class")" -o jsonpath='{.handler}' 2>/dev/null || true)
  fi
  if [[ -z ${handler} ]] || ! "${k[@]}" get node "${cluster}-control-plane" -o json | \
    jq -e --arg handler "${handler}" 'any(.status.runtimeHandlers[]?; .name == $handler and .features.userNamespaces == true)' >/dev/null; then
    python3 "${harness}/probe-crun.py" --state "${state}"
  fi
  python3 "${harness}/jenkins.py" --state "${state}" refresh
fi
