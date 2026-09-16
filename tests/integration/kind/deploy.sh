#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/kind-review}
harness=tests/integration/kind
: "${KUBECONFIG:?Set an explicit harness-only kubeconfig}"
k=(kubectl --kubeconfig "${KUBECONFIG}")
# Requires prepared TLS, source tools and locally loaded harness images. This
# script deploys services; cluster creation/image builds belong to up.sh.
[[ -s ${state}/tls/ca.crt && -s ${state}/tls/tls.key ]] || { echo 'Run up.sh to prepare TLS first' >&2; exit 2; }
"${k[@]}" create namespace factory-harness --dry-run=client -o yaml | "${k[@]}" apply -f -
# Public test credentials only: oidc/harness, isolated to this disposable registry.
"${k[@]}" -n factory-harness create secret generic registry-auth \
  --from-file="htpasswd=${harness}/registry.htpasswd" --dry-run=client -o yaml | "${k[@]}" apply -f -
"${k[@]}" apply -f "${harness}/services.yaml"
"${k[@]}" -n factory-harness create secret generic registry-tls \
  --from-file="ca.crt=${state}/tls/ca.crt" --from-file="tls.key=${state}/tls/tls.key" \
  --dry-run=client -o yaml | "${k[@]}" apply -f -
# Snapshot reviewed files and the harness, excluding ignored credentials/artifacts.
git ls-files --cached --others --exclude-standard -z | tar --null -T - -czf "${state}/source.tar.gz"
"${k[@]}" -n factory-harness create configmap factory-source \
  --from-file="source.tar.gz=${state}/source.tar.gz" --dry-run=client -o yaml | \
  "${k[@]}" apply --server-side --field-manager=factory-harness --force-conflicts -f -
# Server-side apply avoids duplicating the archive in the size-limited
# last-applied annotation. The harness owns this entire source snapshot, including
# when taking over from client-side apply. Remove the older annotation as well.
"${k[@]}" -n factory-harness annotate configmap factory-source kubectl.kubernetes.io/last-applied-configuration-
"${k[@]}" -n factory-harness create configmap jenkins-init \
  --from-file="init.groovy=${harness}/init.groovy" --dry-run=client -o yaml | "${k[@]}" apply -f -
"${k[@]}" -n factory-harness create configmap harness-pipeline \
  --from-file="Pipeline.groovy=${harness}/Pipeline.groovy" --dry-run=client -o yaml | "${k[@]}" apply -f -
python3 - "${state}/jenkins-password" <<'PY'
import pathlib,secrets,sys
p=pathlib.Path(sys.argv[1])
if not p.exists():
    p.write_text(secrets.token_urlsafe(24)); p.chmod(0o600)
PY
"${k[@]}" -n factory-harness create secret generic jenkins-login \
  --from-file="password=${state}/jenkins-password" --dry-run=client -o yaml | "${k[@]}" apply -f -
"${k[@]}" apply -f "${harness}/jenkins.yaml"
"${k[@]}" -n factory-harness rollout status deployment/registry --timeout=180s
skopeo copy --dest-creds oidc:harness --dest-cert-dir "${state}/client-ca" \
  docker://docker.io/library/ubuntu:24.04 "docker://localhost:${FACTORY_HARNESS_REGISTRY_PORT:-15443}/seed:base"
"${k[@]}" -n factory-harness rollout status deployment/jenkins --timeout=300s
python3 "${harness}/jenkins.py" --state "${state}" refresh
printf 'Jenkins: http://127.0.0.1:%s (user review; password in %s/jenkins-password)\n' "${FACTORY_HARNESS_JENKINS_PORT:-18080}" "${state}"
printf 'Run: python3 tests/integration/kind/jenkins.py run\n'
