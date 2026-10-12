#!/usr/bin/env bash
# Deploy Tekton Pipelines and the factory objects into the harness namespace,
# and refresh the source snapshot. up.sh builds the runner image and runs this;
# `make harness-run` runs it again before every run, so the scripts you just
# edited or pulled are what the pods execute.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/harness}
harness=tests/integration/local
namespace=factory-harness
[[ -s ${state}/kubeconfig ]] || { echo "No ${state}/kubeconfig; run make harness-up first" >&2; exit 2; }
k=(kubectl --kubeconfig "${state}/kubeconfig")
"${k[@]}" create namespace "${namespace}" --dry-run=client -o yaml | "${k[@]}" apply -f -
# Snapshot of tracked and untracked-but-not-ignored files: what you would
# commit, without credentials or build output.
git ls-files --cached --others --exclude-standard -z | tar --null -T - -czf "${state}/source.tar.gz"
"${k[@]}" -n "${namespace}" create configmap factory-source \
  --from-file="source.tar.gz=${state}/source.tar.gz" --dry-run=client -o yaml |
  "${k[@]}" apply --server-side --field-manager=factory-harness --force-conflicts -f -
# Tekton Pipelines at the pinned version. Re-applying the same release over an
# install made by docs/local-rancher-desktop.md is a no-op.
tekton_version=$(python3 -c 'import yaml; print(yaml.safe_load(open("tools/versions.lock.yaml"))["tools"]["tekton-pipelines"]["version"])')
"${k[@]}" apply --server-side -f \
  "${TEKTON_PIPELINE_RELEASE_URL:-https://infra.tekton.dev/tekton-releases/pipeline/previous/${tekton_version}/release.yaml}"
"${k[@]}" -n tekton-pipelines rollout status deployment/tekton-pipelines-controller --timeout=300s
"${k[@]}" -n tekton-pipelines rollout status deployment/tekton-pipelines-webhook --timeout=300s

"${k[@]}" -n "${namespace}" apply -f deploy/base/serviceaccounts.yaml
# Settings: deploy/base defaults plus two harness overrides. RPM content comes
# from Red Hat's public UBI CDN (no mirror credential), and behind a
# TLS-inspecting proxy the build mounts the runner's merged trust store.
python3 - "${state}" <<'PYTHON' | "${k[@]}" -n "${namespace}" apply -f -
import pathlib, sys, yaml
settings = yaml.safe_load(open("deploy/base/settings.yaml"))
settings["data"]["FACTORY_RPM_SOURCE_MODE"] = "public-upstream"
if (pathlib.Path(sys.argv[1]) / "ca-bundle").exists():
    settings["data"]["FACTORY_CA_BUNDLE"] = "/etc/pki/tls/certs/ca-bundle.crt"
print(yaml.safe_dump(settings))
PYTHON
# The build Task binds these by secretKeyRef, so its pod cannot start without
# them. Nothing on the harness path reads the values: prepare supplies the base
# archive (no registry login) and RPMs come from the public CDN. Never put real
# credentials here.
"${k[@]}" -n "${namespace}" create secret generic factory-artifactory-read \
  --from-literal=token=harness-placeholder --dry-run=client -o yaml | "${k[@]}" apply -f -
"${k[@]}" -n "${namespace}" create secret generic factory-rpm-mirror \
  --from-literal=username=harness-placeholder --from-literal=password=harness-placeholder \
  --dry-run=client -o yaml | "${k[@]}" apply -f -
"${k[@]}" -n "${namespace}" apply -f .tekton/tasks/ -f .tekton/pipelines/
"${k[@]}" -n "${namespace}" apply -f "${harness}/harness-pipeline.yaml"
