#!/usr/bin/env bash
set -euo pipefail
work=smoke/build
mkdir -p "${work}/context" /tmp/factory-runtime
chmod 700 /tmp/factory-runtime
registry=${FACTORY_HARNESS_REGISTRY:-registry.factory-harness.svc.cluster.local:5000}
skopeo copy --src-creds oidc:harness "docker://${registry}/seed:base" "oci-archive:${work}/base.oci.tar"
digest=$(skopeo inspect "oci-archive:${work}/base.oci.tar" | jq -er .Digest)
printf 'ARG BASE_REF\nFROM ${BASE_REF}\nRUN echo kind-harness >/factory-harness.txt\n' >"${work}/context/Dockerfile"
git -C "${work}/context" init -q
git -C "${work}/context" add Dockerfile
git -C "${work}/context" -c user.name=Harness -c user.email=harness@localhost commit -qm fixture
revision=$(git -C "${work}/context" rev-parse HEAD)
printf 'FACTORY_IMAGE=ubi9-minimal\nSOURCE_REVISION=%s\nBASE_REF=%s/seed:base\nBASE_DIGEST=%s\nRPM_REPOMD_DIGEST=sha256:fixture\n' \
  "${revision}" "${registry}" "${digest}" >"${work}/build.env"
printf '{"localDevelopment":true,"resources":[]}\n' >"${work}/resource-lock.json"
# This is explicitly a synthetic development build: no RPMs, production locks,
# proprietary applications, or real security assessment are involved.
export FACTORY_RPM_BASE_URL="https://${registry}/unused"
export RPM_SNAPSHOT_UBI9_ID=harness
export FACTORY_BUILD_NETWORK=none
export FACTORY_BUILDKIT_NO_PROCESS_SANDBOX=true
scripts/build_image.sh catalog/images/ubi9-minimal.yaml "${work}"
jq -e '.digest | startswith("sha256:")' "${work}/image-metadata.json" >/dev/null
# Confirm the actual produced image can be loaded and executed by rootless
# Podman, independently of scanner mocks.
podman load -i "${work}/image.oci.tar"
# shellcheck disable=SC1090
source "${work}/build.env"
[[ $(podman run --rm --network=none --cgroups=disabled --entrypoint /bin/cat "${LOCAL_IMAGE_REF}" /factory-harness.txt) == kind-harness ]]
# A real failing RUN must propagate failure and leave no per-build state.
printf 'ARG BASE_REF\nFROM ${BASE_REF}\nRUN exit 37\n' >"${work}/context/Dockerfile"
if scripts/build_image.sh catalog/images/ubi9-minimal.yaml "${work}" >"${work}/expected-failure.log" 2>&1; then
  echo 'Build unexpectedly accepted a failing RUN' >&2; exit 1
fi
if find /tmp -maxdepth 1 -name 'factory-buildkit.*' -print -quit | grep -q .; then
  echo 'BuildKit state survived a failed RUN' >&2; exit 1
fi
printf '{"buildPassed":true,"podmanExecutionPassed":true,"failingRunRejected":true,"cleanupPassed":true}\n' >"${work}/build-smoke-result.json"
