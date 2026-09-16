#!/usr/bin/env bash
# Read-only inspection of the dedicated rootful kind node; run from your terminal.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/proc-fixed-kind}
node=$(python3 - "${state}/settings.json" <<'PY'
import json, re, sys
settings = json.load(open(sys.argv[1]))
if settings.get('provider') != 'podman' or settings.get('rootful') is not True:
    raise SystemExit('This diagnostic requires a saved rootful Podman harness')
cluster = settings['cluster']
if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', cluster):
    raise SystemExit('Invalid saved cluster name')
print(cluster + '-control-plane')
PY
)
sudo -n true || { echo 'Run sudo -v in this terminal, then retry.' >&2; exit 2; }
output="${state}/startup-diagnostics"
mkdir -p "${output}"
sudo -n podman inspect "${node}" --format '{{.HostConfig.Privileged}} {{.HostConfig.SecurityOpt}}' >"${output}/outer-security.txt"
sudo -n podman exec "${node}" sh -ec '
  runc --version
  cat /proc/self/uid_map /proc/self/gid_map
  cat /proc/self/attr/current
  findmnt -R /sys
  cat /etc/containerd/cri-base.json
' >"${output}/node-runtime.txt"
# Only security denials, rather than an unrestricted host journal dump.
sudo -n journalctl -k --since '-20 minutes' --no-pager |
  awk '/apparmor="DENIED"|apparmor="ALLOWED"|avc: *denied/' >"${output}/security-denials.txt"
printf 'Saved read-only diagnostics to %s\n' "${output}"
