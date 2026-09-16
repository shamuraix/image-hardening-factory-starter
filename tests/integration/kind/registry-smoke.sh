#!/usr/bin/env bash
set -euo pipefail
work=smoke/registry
evidence="${work}/evidence"
mkdir -p "${evidence}"/{scans/fcs,compliance,tests} /tmp/factory-runtime
export ARTIFACTORY_REGISTRY=registry.factory-harness.svc.cluster.local:5000
export ARTIFACTORY_WRITE_TOKEN=harness ARTIFACTORY_SIGN_TOKEN=harness ARTIFACTORY_RELEASE_TOKEN=harness
export FACTORY_PROTECTED_PUBLISH=true FACTORY_RELEASE_ENV=commercial FACTORY_IMAGE=ubi9-minimal
export FACTORY_QUARANTINE_REPOSITORY=quarantine FACTORY_RELEASE_REPOSITORY=release FACTORY_IMAGE_PATH=harness/${BUILD_NUMBER:-local}
export FACTORY_BUILD_ID=${FACTORY_BUILD_ID:-kind-review}
skopeo copy --src-creds oidc:harness "docker://${ARTIFACTORY_REGISTRY}/seed:base" "oci-archive:${work}/image.oci.tar"
digest=$(skopeo inspect "oci-archive:${work}/image.oci.tar" | jq -er .Digest)
# Synthetic predicates exercise transport and verification, not security scans.
python3 - "${work}" "${digest}" <<'PY'
import json,sys,pathlib,datetime
w=pathlib.Path(sys.argv[1]);d=sys.argv[2];now=datetime.datetime.now(datetime.timezone.utc).isoformat()
def put(name,data): (w/name).write_text(json.dumps(data))
put('image-metadata.json',dict(digest=d,sourceRevision='a'*40,factoryRevision='fixture',baseRef='fixture',baseDigest=d,rpmSnapshot='fixture',rpmRepomdDigest='sha256:fixture',startedOn=now,finishedOn=now,builder='kind-harness'))
put('resource-lock.json',{'resources':[],'syntheticHarness':True})
put('evidence/gate-result.json',{'allow':True,'deny':[]})
for p in ['evidence/sbom.cdx.json','evidence/scans/fcs/sbom.cdx.json']: put(p,{'bomFormat':'CycloneDX','specVersion':'1.6','components':[]})
put('evidence/sbom.spdx.json',{'spdxVersion':'SPDX-2.3','packages':[]})
put('evidence/findings.json',{'findings':[]})
for p in ['evidence/scans/fcs/assessment.json','evidence/scans/fcs/status.json','evidence/compliance/result.json','evidence/tests/result.json']: put(p,{'syntheticHarness':True,'passed':True,'assessmentPassed':True,'digest':d})
PY
printf '{"allow":false}\n' >"${evidence}/gate-result.json"
if scripts/import_image.sh catalog/images/ubi9-minimal.yaml "${work}"; then
  echo 'Denied gate incorrectly imported' >&2; exit 1
fi
printf '{"allow":true,"deny":[]}\n' >"${evidence}/gate-result.json"
cp "${work}/resource-lock.json" "${work}/resource-lock.saved"
printf '{"localDevelopment":true,"resources":[]}\n' >"${work}/resource-lock.json"
if scripts/import_image.sh catalog/images/ubi9-minimal.yaml "${work}"; then
  echo 'Development lock incorrectly imported' >&2; exit 1
fi
mv "${work}/resource-lock.saved" "${work}/resource-lock.json"
import_passed=true
scripts/import_image.sh catalog/images/ubi9-minimal.yaml "${work}"
private=$(mktemp -d)
trap 'rm -rf "${private}"' EXIT
if printf 'wrong-password' | skopeo login --authfile "${private}/invalid.json" \
  --username oidc --password-stdin "${ARTIFACTORY_REGISTRY}"; then
  echo 'Registry accepted an invalid password' >&2; exit 1
fi
export COSIGN_PASSWORD=harness-only
cosign generate-key-pair --output-key-prefix "${private}/cosign" >/dev/null
export COSIGN_KEY_PATH="${private}/cosign.key" COSIGN_PUBLIC_KEY="${private}/cosign.pub"
signing_passed=false
promotion_passed=false
if scripts/sign_and_attest.sh catalog/images/ubi9-minimal.yaml "${work}"; then
  signing_passed=true
  export DOCKER_CONFIG="${private}/auth"
  mkdir -p "${DOCKER_CONFIG}"
  export REGISTRY_AUTH_FILE="${DOCKER_CONFIG}/config.json"
  printf '%s' "${ARTIFACTORY_WRITE_TOKEN}" | skopeo login --authfile "${REGISTRY_AUTH_FILE}" \
    --username oidc --password-stdin "${ARTIFACTORY_REGISTRY}"
  unsigned="${ARTIFACTORY_REGISTRY}/unsigned/${FACTORY_IMAGE_PATH}:fixture"
  skopeo copy --authfile "${REGISTRY_AUTH_FILE}" --preserve-digests \
    "oci-archive:${work}/image.oci.tar" "docker://${unsigned}"
  if scripts/verify_release_evidence.sh "${unsigned}@${digest}" >/dev/null 2>&1; then
    echo 'Unsigned image was accepted' >&2; exit 1
  fi
  if scripts/promote_image.sh catalog/images/ubi9-minimal.yaml "${work}" &&
    scripts/promote_image.sh catalog/images/ubi9-minimal.yaml "${work}" &&
    jq -e --arg digest "${digest}" '.promoted == true and .digest == $digest' "${work}/promotion-result.json" >/dev/null; then
    promotion_passed=true
  fi
fi
jq -n --argjson imported "${import_passed}" --argjson signed "${signing_passed}" \
  --argjson promoted "${promotion_passed}" \
  '{syntheticHarness:true,importPassed:$imported,signingPassed:$signed,promotionPassed:$promoted}' \
  >"${work}/registry-smoke-result.json"
cat "${work}/registry-smoke-result.json"
[[ ${import_passed} == true && ${signing_passed} == true && ${promotion_passed} == true ]]
