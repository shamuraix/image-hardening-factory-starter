#!/usr/bin/env bash
# Linux host probe: a valid image signature must not substitute for attestations.
set -euo pipefail
state=${FACTORY_HARNESS_STATE:-.local-factory/kind-review}
registry="localhost:${FACTORY_HARNESS_REGISTRY_PORT:-15443}"
export SSL_CERT_FILE="$(cd "${state}/client-ca" && pwd)/ca.crt"
private=$(mktemp -d)
trap 'rm -rf "${private}"' EXIT
export DOCKER_CONFIG="${private}" COSIGN_PASSWORD=harness-only
printf 'harness' | skopeo login --authfile "${private}/config.json" --username oidc --password-stdin "${registry}"
export REGISTRY_AUTH_FILE="${private}/config.json"
ref="${registry}/missing-evidence/$(date +%s)-$$:fixture"
skopeo copy --authfile "${REGISTRY_AUTH_FILE}" --preserve-digests "docker://${registry}/seed:base" "docker://${ref}"
digest=$(skopeo inspect --authfile "${REGISTRY_AUTH_FILE}" "docker://${ref}" | jq -er .Digest)
cosign generate-key-pair --output-key-prefix "${private}/key" >/dev/null
cosign sign --yes --tlog-upload=false --key "${private}/key.key" "${ref}@${digest}"
export COSIGN_PUBLIC_KEY="${private}/key.pub"
cosign verify --insecure-ignore-tlog --key "${COSIGN_PUBLIC_KEY}" "${ref}@${digest}" >/dev/null
if scripts/verify_release_evidence.sh "${ref}@${digest}" >"${state}/missing-evidence.log" 2>&1; then
  echo 'Signed image without required attestations was incorrectly accepted' >&2; exit 1
fi
printf '{"signatureValid":true,"missingAttestationsRejected":true}\n' >"${state}/missing-evidence-result.json"
