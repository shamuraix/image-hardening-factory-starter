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
# Tekton Pipelines (version pinned in tools/versions.lock.yaml).
tekton_version=$(yq -r '.tools."tekton-pipelines".version' tools/versions.lock.yaml)
"${k[@]}" apply --server-side -f \
  "${TEKTON_PIPELINE_RELEASE_URL:-https://github.com/tektoncd/pipeline/releases/download/${tekton_version}/release.yaml}"
"${k[@]}" -n tekton-pipelines rollout status deployment/tekton-pipelines-controller --timeout=300s
"${k[@]}" -n tekton-pipelines rollout status deployment/tekton-pipelines-webhook --timeout=300s
# Factory ServiceAccounts, settings, Tasks and Pipelines in the harness namespace.
"${k[@]}" -n factory-harness apply -f deploy/base/serviceaccounts.yaml
"${k[@]}" -n factory-harness apply -f deploy/base/settings.yaml
"${k[@]}" -n factory-harness apply -f .tekton/tasks/ -f .tekton/pipelines/
"${k[@]}" -n factory-harness apply -f "${harness}/harness-pipeline.yaml"
"${k[@]}" -n factory-harness rollout status deployment/registry --timeout=180s
skopeo copy --dest-creds oidc:harness --dest-cert-dir "${state}/client-ca" \
  docker://docker.io/library/ubuntu:24.04 "docker://localhost:${FACTORY_HARNESS_REGISTRY_PORT:-15443}/seed:base"
printf 'Run: python3 tests/integration/kind/tekton.py --state %s run\n' "${state}"
