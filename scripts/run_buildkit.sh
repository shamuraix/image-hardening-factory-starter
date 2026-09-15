#!/usr/bin/env bash
set -euo pipefail

# Re-enter inside RootlessKit so subordinate-owned snapshots can be removed
# before the user namespace exits, including after failed or interrupted builds.
if [[ ${1:-} == --inside-rootlesskit ]]; then
  [[ -n ${ROOTLESSKIT_STATE_DIR:-} && $(id -u) == 0 ]] || {
    echo "the internal BuildKit entry point requires RootlessKit" >&2
    exit 2
  }
  shift
  state=${1:?private state directory is required}
  sandbox=${2:?process sandbox setting is required}
  network=${3:?network mode is required}
  shift 3
  daemon_pid=
  client_pid=
  cleanup() {
    if [[ -n ${client_pid} ]]; then
      kill "${client_pid}" 2>/dev/null || true
      wait "${client_pid}" 2>/dev/null || true
    fi
    if [[ -n ${daemon_pid} ]]; then
      kill "${daemon_pid}" 2>/dev/null || true
      wait "${daemon_pid}" 2>/dev/null || true
    fi
    rm -rf "${state}/data"
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  address="unix://${state}/buildkitd.sock"
  daemon_args=(
    --rootless --root "${state}/data" --addr "${address}"
    --config "${state}/buildkitd.toml"
    --oci-worker=true --containerd-worker=false
    --oci-worker-snapshotter=native --oci-worker-net=host
    --oci-worker-binary=buildkit-runc
  )
  [[ "${sandbox}" == true ]] && daemon_args+=(--oci-worker-no-process-sandbox)
  [[ "${network}" == host ]] && daemon_args+=(--allow-insecure-entitlement network.host)
  buildkitd "${daemon_args[@]}" >"${state}/buildkitd.log" 2>&1 &
  daemon_pid=$!
  ready=false
  for ((attempt = 0; attempt < 60; attempt++)); do
    if buildctl --addr "${address}" debug workers >/dev/null 2>&1; then
      ready=true
      break
    fi
    kill -0 "${daemon_pid}" 2>/dev/null || break
    sleep 1
  done
  if [[ "${ready}" != true ]]; then
    echo "rootless BuildKit did not become ready; check node user namespaces and pod security profiles" >&2
    cat "${state}/buildkitd.log" >&2
    exit 1
  fi
  buildctl --addr "${address}" "$@" &
  client_pid=$!
  status=0
  wait "${client_pid}" || status=$?
  client_pid=
  exit "${status}"
fi

[[ $(id -u) != 0 ]] || { echo "BuildKit must run rootless" >&2; exit 1; }
for tool in rootlesskit buildkitd buildctl buildkit-runc; do
  command -v "${tool}" >/dev/null || { echo "required command is missing: ${tool}" >&2; exit 2; }
done
[[ ${1:-} == build ]] || { echo "usage: scripts/run_buildkit.sh build [buildctl options]" >&2; exit 2; }
sandbox=${FACTORY_BUILDKIT_NO_PROCESS_SANDBOX:-false}
case "${sandbox}" in
  true|false) ;;
  *) echo "FACTORY_BUILDKIT_NO_PROCESS_SANDBOX must be true or false" >&2; exit 2 ;;
esac
network=${FACTORY_BUILD_NETWORK:-default}
case "${network}" in
  default|none|host) ;;
  *) echo "FACTORY_BUILD_NETWORK must be default, none, or host" >&2; exit 2 ;;
esac
umask 077
state=$(mktemp -d /tmp/factory-buildkit.XXXXXX)
rootless_pid=
cleanup_outer() {
  if [[ -n ${rootless_pid} ]]; then
    kill "${rootless_pid}" 2>/dev/null || true
    wait "${rootless_pid}" 2>/dev/null || true
  fi
  rm -rf "${state}"
}
trap cleanup_outer EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Do not inherit a user's daemon configuration, remote socket, or shared cache.
: >"${state}/buildkitd.toml"
mkdir "${state}/runtime"
rootless_args=(--state-dir="${state}/rootlesskit" --net=host --copy-up=/etc --copy-up=/run)
# A PID namespace would remount /proc and reintroduce the nested-container
# restriction that no-process-sandbox avoids. Kubernetes owns pod cleanup.
[[ "${sandbox}" == false ]] && rootless_args+=(--pidns)
XDG_RUNTIME_DIR="${state}/runtime" rootlesskit "${rootless_args[@]}" \
  "$(realpath "${BASH_SOURCE[0]}")" --inside-rootlesskit "${state}" "${sandbox}" "${network}" "$@" &
rootless_pid=$!
status=0
wait "${rootless_pid}" || status=$?
rootless_pid=
exit "${status}"
