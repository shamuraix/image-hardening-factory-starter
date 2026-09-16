#!/usr/bin/env bash
# Authenticate in your terminal with `sudo -v`, then run this as your normal user.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
[[ $(id -u) != 0 ]] || { echo 'Run this script as your normal user after sudo -v.' >&2; exit 2; }
sudo -n true || { echo 'Run sudo -v in this terminal, then retry this script.' >&2; exit 2; }
state=${FACTORY_ROOTFUL_TEST_STATE:-.local-factory/rootful-preflight}
archive=${FACTORY_ROOTFUL_RUNNER_ARCHIVE:-.local-factory/kind-review/runner.tar}
[[ -s ${archive} ]] || { echo "Runner archive missing: ${archive}; run the existing harness bootstrap first." >&2; exit 2; }
mkdir -p "${state}/build" "${state}/results"
state=$(cd "${state}" && pwd)
name="factory-rootful-preflight-$(id -u)-$$"
image="localhost/factory-rootful-preflight:review"
apparmor_profile=${FACTORY_ROOTFUL_APPARMOR_PROFILE:-unconfined}
sudo -n podman info --format '{{.Host.Security.Rootless}}' >"${state}/rootless.txt"
[[ $(cat "${state}/rootless.txt") == false ]] || { echo 'Expected rootful Podman' >&2; exit 1; }
sudo -n podman load -i "${archive}" >"${state}/load.log" 2>&1
cp tests/integration/kind/configure-uidmap.py "${state}/build/configure-uidmap.py"
cat >"${state}/build/Containerfile" <<'CONTAINERFILE'
FROM localhost/factory-review-runner:review
USER 0
RUN printf 'factory:100000:65536\n' >/etc/subuid \
    && printf 'factory:100000:65536\n' >/etc/subgid
COPY configure-uidmap.py /tmp/configure-uidmap.py
RUN python3 /tmp/configure-uidmap.py && rm /tmp/configure-uidmap.py
USER 10001
CONTAINERFILE
sudo -n podman build --pull=never --network=none -t "${image}" \
  -f "${state}/build/Containerfile" "${state}/build" >"${state}/build.log" 2>&1
cleanup() { sudo -n podman rm -f "${name}" >/dev/null 2>&1 || true; }
trap cleanup EXIT
# Match the runner's unconfined syscall/LSM profiles, without --privileged or UID 0.
sudo -n podman create --name "${name}" --pull=never --user 10001:10001 \
  --security-opt seccomp=unconfined --security-opt "apparmor=${apparmor_profile}" \
  --security-opt 'unmask=/proc/*' --network=none \
  --volume "${PWD}:/workspace:ro" --workdir /workspace \
  --env FACTORY_BUILDKIT_NO_PROCESS_SANDBOX=true \
  --env FACTORY_BUILD_NETWORK=none \
  "${image}" bash scripts/runtime_preflight.sh /tmp/rootful-results \
  >"${state}/container-id"
status=0
sudo -n podman start --attach "${name}" >"${state}/console.log" 2>&1 || status=$?
sudo -n podman inspect "${name}" --format '{{.State.ExitCode}}' >"${state}/exit-code"
# Stream the container files so the invoking user owns the collected diagnostics.
sudo -n podman cp "${name}:/tmp/rootful-results/." - | tar --no-same-owner -xf - -C "${state}/results"
cat "${state}/results/result.json"
printf 'Container exit: %s; diagnostics: %s\n' "$(cat "${state}/exit-code")" "${state}"
[[ ${status} == 0 && $(cat "${state}/exit-code") == 0 ]]
