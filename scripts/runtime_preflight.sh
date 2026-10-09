#!/usr/bin/env bash
set -euo pipefail
output=${1:?diagnostic output directory is required}
mkdir -p "${output}"
printf '{"passed":false}\n' >"${output}/result.json"
{
  id
  uname -smr
  printf '\nUID/GID mappings\n'
  cat /proc/self/uid_map /proc/self/gid_map
  printf '\nUser namespace policy\n'
  for setting in /proc/sys/kernel/apparmor_restrict_unprivileged_userns /proc/sys/kernel/unprivileged_userns_clone /proc/sys/user/max_user_namespaces; do
    if [[ -r ${setting} ]]; then
      printf '%s=' "${setting}"
      cat "${setting}"
    fi
  done
  printf '\nSubordinate ranges\n'
  cat /etc/subuid /etc/subgid
  printf '\nMapping helper modes and file capabilities\n'
  ls -l /usr/bin/newuidmap /usr/bin/newgidmap
  python3 - <<'PYTHON'
import os
for name in ("/usr/bin/newuidmap", "/usr/bin/newgidmap"):
    try:
        print(name, "security.capability=" + os.getxattr(name, "security.capability").hex())
    except OSError as error:
        print(name, "capability xattr unavailable:", error)
PYTHON
  printf '\nSecurity restrictions\n'
  sed -n '/^Cap/p;/^NoNewPrivs/p;/^Seccomp/p' /proc/self/status
  cat /proc/self/attr/current 2>/dev/null || true
  for tool in rootlesskit buildkitd buildctl podman; do
    "${tool}" --version
  done
} >"${output}/runtime.txt" 2>&1
[[ $(id -u) != 0 ]] || { echo 'Factory runner must be non-root' >&2; exit 1; }
# The setuid mapping helpers receive only the capabilities in this process's
# bounding set (bit 6 CAP_SETGID, bit 7 CAP_SETUID). Explain that before
# RootlessKit reports a bare "Permission denied" from the kernel.
bounding=$(sed -n 's/^CapBnd:[[:space:]]*//p' /proc/self/status)
if (( (0x${bounding} & 0xc0) != 0xc0 )); then
  echo "capability bounding set ${bounding} lacks CAP_SETUID/CAP_SETGID; the setuid newuidmap/newgidmap helpers cannot map subordinate IDs. The build and test run steps must keep capabilities.add: [SETUID, SETGID] (docs/configuration.md)." >&2
  exit 1
fi
for helper in /usr/bin/newuidmap /usr/bin/newgidmap; do
  [[ -u ${helper} ]] || { echo "${helper} is not setuid; file capabilities are ignored inside a pod user namespace (toolchain/Containerfile.factory-runner)" >&2; exit 1; }
  grep -aq 'Could not set caps' "${helper}" || {
    echo "${helper} was built without libcap, so as a setuid program it would need CAP_SYS_ADMIN and CAP_DAC_OVERRIDE; the runner image must install the libcap-aware build (toolchain/Containerfile.factory-runner)" >&2
    exit 1
  }
done
# Exercise the same mapping helpers used by the real BuildKit launcher. A
# successful unshare -Ur alone is insufficient to prove subordinate mappings.
if ! timeout 20 rootlesskit --net=host --copy-up=/etc --copy-up=/run \
  sh -c 'cat /proc/self/uid_map /proc/self/gid_map' >"${output}/rootlesskit.log" 2>&1; then
  cat "${output}/rootlesskit.log" >&2
  echo 'RootlessKit namespace/mapping probe failed. Inspect mapping-helper file capabilities, outer UID/GID ranges and node security policy in runtime.txt. See docs/local-kubernetes-testing.md; keep the factory pod non-root.' >&2
  exit 1
fi
if ! timeout 20 podman unshare cat /proc/self/uid_map >"${output}/podman.log" 2>&1; then
  cat "${output}/podman.log" >&2
  echo 'Podman user namespace probe failed; inspect runtime.txt and podman.log.' >&2
  exit 1
fi
# BuildKit's no-process-sandbox mode does not exercise the private proc mount
# required by Podman image tests. Probe that boundary before the expensive build.
if ! timeout 20 rootlesskit --net=host --copy-up=/etc --copy-up=/run \
  unshare --mount --pid --fork --mount-proc sh -ec 'test -r /proc/1/status; cat /proc/self/uid_map' \
  >"${output}/proc-mount.log" 2>&1; then
  cat "${output}/proc-mount.log" >&2
  echo 'Nested /proc mount failed. Kubernetes runners require hostUsers: false and procMount: Unmasked, with adequate node and pod UID/GID ranges. See docs/local-kubernetes-testing.md.' >&2
  exit 1
fi
# A network-free scratch build exercises daemon startup and snapshot/export.
probe=$(mktemp -d)
trap 'rm -rf "${probe}"' EXIT
printf 'FROM scratch\nCOPY marker /marker\n' >"${probe}/Dockerfile"
printf 'factory-runtime-preflight\n' >"${probe}/marker"
timeout 90 scripts/run_buildkit.sh build --frontend dockerfile.v0 \
  --local "context=${probe}" --local "dockerfile=${probe}" \
  --output "type=oci,dest=${probe}/probe.tar" >"${output}/buildkit.log" 2>&1 || {
  cat "${output}/buildkit.log" >&2; exit 1;
}
printf '{"passed":true}\n' >"${output}/result.json"
