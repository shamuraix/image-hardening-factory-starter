#!/usr/bin/env bash
set -euo pipefail
subject=${1:?digest-qualified subject is required}
: "${COSIGN_PUBLIC_KEY:?}"
prefix=${FACTORY_PREDICATE_TYPE_PREFIX:-urn:image-hardening-factory:predicate}
cosign verify --key "${COSIGN_PUBLIC_KEY}" --insecure-ignore-tlog "${subject}" >/dev/null
# Check signature presence for every release-critical predicate. Cosign verifies
# each in-toto subject against the requested image digest.
for type in https://slsa.dev/provenance/v1 https://cyclonedx.org/bom \
  https://spdx.dev/Document "${prefix}:fcs-decision:v1" \
  "${prefix}:compliance:v1" "${prefix}:test:v1" "${prefix}:resource-lock:v1"; do
  cosign verify-attestation --key "${COSIGN_PUBLIC_KEY}" --insecure-ignore-tlog \
    --type "${type}" "${subject}" >/dev/null
done
cosign verify-attestation --key "${COSIGN_PUBLIC_KEY}" --insecure-ignore-tlog \
  --type "${prefix}:gate:v1" "${subject}" | \
  jq -se 'any(.[]; (.payload | @base64d | fromjson).predicate.allow == true)' >/dev/null
if [[ ${FACTORY_RELEASE_ENV:-commercial} =~ ^gov[12]$ ]]; then
  cosign verify-attestation --key "${COSIGN_PUBLIC_KEY}" --insecure-ignore-tlog \
    --type "${prefix}:approval:v1" "${subject}" | \
    jq -se --arg environment "${FACTORY_RELEASE_ENV}" --arg approver "${FACTORY_APPROVER_ID:?}" \
      'any(.[]; (.payload | @base64d | fromjson).predicate | .environment == $environment and .approver == $approver)' >/dev/null
fi
