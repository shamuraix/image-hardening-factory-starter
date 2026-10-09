#!/usr/bin/env bash
# Deploy Tekton Pipelines and the factory objects into the harness cluster, and
# refresh the source snapshot. Cluster creation and the runner image belong to
# up.sh; rerun this alone after editing Tasks, Pipelines, or scripts.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/kind-review}
harness=tests/integration/kind
: "${KUBECONFIG:?Set an explicit harness-only kubeconfig}"
k=(kubectl --kubeconfig "${KUBECONFIG}")
"${k[@]}" create namespace factory-harness --dry-run=client -o yaml | "${k[@]}" apply -f -
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
tekton_version=$(python3 -c 'import yaml; print(yaml.safe_load(open("tools/versions.lock.yaml"))["tools"]["tekton-pipelines"]["version"])')
"${k[@]}" apply --server-side -f \
  "${TEKTON_PIPELINE_RELEASE_URL:-https://github.com/tektoncd/pipeline/releases/download/${tekton_version}/release.yaml}"
"${k[@]}" -n tekton-pipelines rollout status deployment/tekton-pipelines-controller --timeout=300s
"${k[@]}" -n tekton-pipelines rollout status deployment/tekton-pipelines-webhook --timeout=300s
# Factory ServiceAccounts and settings. The harness overrides two settings: RPM
# content comes from Red Hat's public UBI CDN (no mirror credential), and the
# catalog directory is unchanged. Everything else keeps the placeholder values.
"${k[@]}" -n factory-harness apply -f deploy/base/serviceaccounts.yaml
python3 - "${state}" <<'PYTHON' | "${k[@]}" -n factory-harness apply -f -
import pathlib, sys, yaml
settings = yaml.safe_load(open("deploy/base/settings.yaml"))
settings["data"]["FACTORY_RPM_SOURCE_MODE"] = "public-upstream"
if (pathlib.Path(sys.argv[1]) / "ca-bundle").exists():
    # The runner was built with extra CAs (FACTORY_CA_BUNDLE); the images it
    # builds reach the network through the same proxy, so mount the runner's
    # merged trust store into their RUN steps.
    settings["data"]["FACTORY_CA_BUNDLE"] = "/etc/pki/tls/certs/ca-bundle.crt"
print(yaml.safe_dump(settings))
PYTHON
# The build Task binds two Secrets by secretKeyRef, so the pod cannot start
# without them even though neither value is read on the harness path: the base
# comes from prepare's base.oci.tar (no registry login) and RPMs come from the
# public CDN (no mirror credential). Placeholders let the pod start; the Task
# itself stays strict so a missing credential in production fails at pod
# creation with a clear reason. Never put real values here.
"${k[@]}" -n factory-harness create secret generic factory-artifactory-read \
  --from-literal=token=harness-placeholder --dry-run=client -o yaml | "${k[@]}" apply -f -
"${k[@]}" -n factory-harness create secret generic factory-rpm-mirror \
  --from-literal=username=harness-placeholder --from-literal=password=harness-placeholder \
  --dry-run=client -o yaml | "${k[@]}" apply -f -
"${k[@]}" -n factory-harness apply -f .tekton/tasks/ -f .tekton/pipelines/
"${k[@]}" -n factory-harness apply -f "${harness}/harness-pipeline.yaml"
printf 'Run: python3 tests/integration/kind/tekton.py --state %s run\n' "${state}"
