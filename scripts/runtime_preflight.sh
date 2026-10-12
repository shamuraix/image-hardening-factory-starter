#!/usr/bin/env bash
# Probe the build step's runtime before the expensive build: non-root user,
# capability bounding set, the file-capability mapping helpers, a RootlessKit
# user namespace with subordinate mappings, and a network-free BuildKit build.
# Diagnostics land in <output>/runtime.txt for triage either way.
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
  getcap /usr/bin/newuidmap /usr/bin/newgidmap
  printf '\nSecurity restrictions\n'
  sed -n '/^Cap/p;/^NoNewPrivs/p;/^Seccomp/p' /proc/self/status
  cat /proc/self/attr/current 2>/dev/null || true
  for tool in rootlesskit buildkitd buildctl; do
    "${tool}" --version
  done
} >"${output}/runtime.txt" 2>&1
[[ $(id -u) != 0 ]] || { echo 'Factory runner must be non-root' >&2; exit 1; }
# File capabilities are granted only within this process's bounding set
# (bit 6 CAP_SETGID, bit 7 CAP_SETUID) and only without no_new_privs. Explain
# that before RootlessKit reports a bare "Permission denied" from the kernel.
bounding=$(sed -n 's/^CapBnd:[[:space:]]*//p' /proc/self/status)
if (( (0x${bounding} & 0xc0) != 0xc0 )); then
  echo "capability bounding set ${bounding} lacks CAP_SETUID/CAP_SETGID; the newuidmap/newgidmap file capabilities cannot take effect. The build run step must keep capabilities.add: [SETUID, SETGID] (docs/configuration.md)." >&2
  exit 1
fi
if [[ $(sed -n 's/^NoNewPrivs:[[:space:]]*//p' /proc/self/status) != 0 ]]; then
  echo 'no_new_privs is set, which disables the mapping helpers'"'"' file capabilities; the build run step needs allowPrivilegeEscalation: true (docs/configuration.md).' >&2
  exit 1
fi
getcap /usr/bin/newuidmap | grep -q 'cap_setuid=ep' && getcap /usr/bin/newgidmap | grep -q 'cap_setgid=ep' || {
  echo 'newuidmap/newgidmap lack their cap_setuid/cap_setgid file capabilities; rebuild the runner image (toolchain/Containerfile.factory-runner) with a builder that preserves security.capability xattrs.' >&2
  exit 1
}
# Exercise the same mapping helpers used by the real BuildKit launcher. A
# successful unshare -Ur alone is insufficient to prove subordinate mappings.
if ! timeout 20 rootlesskit --net=host --copy-up=/etc --copy-up=/run \
  sh -c 'cat /proc/self/uid_map /proc/self/gid_map' >"${output}/rootlesskit.log" 2>&1; then
  cat "${output}/rootlesskit.log" >&2
  echo 'RootlessKit namespace/mapping probe failed. Inspect mapping-helper file capabilities, the node user-namespace policy, and the AppArmor/seccomp profiles in runtime.txt (docs/configuration.md); keep the factory pod non-root.' >&2
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
