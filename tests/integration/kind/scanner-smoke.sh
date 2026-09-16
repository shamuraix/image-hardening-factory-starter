#!/usr/bin/env bash
# Run on Linux with the pinned harness tools on PATH and a populated offline DB.
# Compliance/product outputs below are explicit fixtures, not assessments.
set -euo pipefail
state=${FACTORY_HARNESS_STATE:-.local-factory/kind-review}
work="${state}/scanner-smoke"
evidence="${work}/evidence"
mkdir -p "${evidence}"/{compliance,tests}
export ARTIFACTORY_REGISTRY="localhost:${FACTORY_HARNESS_REGISTRY_PORT:-15443}"
export SSL_CERT_FILE="$(cd "${state}/client-ca" && pwd)/ca.crt"
export ARTIFACTORY_WRITE_TOKEN=harness ARTIFACTORY_SIGN_TOKEN=harness ARTIFACTORY_RELEASE_TOKEN=harness
export FACTORY_PROTECTED_PUBLISH=true FACTORY_RELEASE_ENV=commercial FACTORY_IMAGE=scanner-fixture
export FACTORY_SCANNER_BACKEND=grype FACTORY_CATALOG_FILE=catalog/images/ubi9-minimal.yaml
export FACTORY_QUARANTINE_REPOSITORY=quarantine FACTORY_RELEASE_REPOSITORY=release
export FACTORY_IMAGE_PATH="scanner-fixture/$(date +%s)-$$" FACTORY_BUILD_ID=scanner-fixture
export GRYPE_DB_CACHE_DIR="$(cd "${state}/security-data/grype" && pwd)"
export FACTORY_KEV_PATH="$(cd "${state}/security-data/advisories" && pwd)/cisa-kev.json"
skopeo copy --src-creds oidc:harness --src-cert-dir "${state}/client-ca" \
  "docker://${ARTIFACTORY_REGISTRY}/seed:base" "oci-archive:${work}/image.oci.tar"
skopeo inspect "oci-archive:${work}/image.oci.tar" | jq '{digest:.Digest}' >"${work}/image-metadata.json"
scripts/generate_sbom.sh "${work}/image.oci.tar" "${evidence}"
scripts/grype_scan_image.sh "${work}"
for target in compliance tests; do
  printf '{"passed":true,"syntheticHarness":true}\n' >"${evidence}/${target}/result.json"
done
scripts/evaluate_gate.sh "${FACTORY_CATALOG_FILE}" "${work}"
# Populate provenance inputs explicitly as test fixtures.
python3 - "${work}" <<'PY'
import datetime,json,pathlib,sys
w=pathlib.Path(sys.argv[1]); meta=json.loads((w/'image-metadata.json').read_text())
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
meta.update(sourceRevision='a'*40,factoryRevision='fixture',baseRef='fixture',baseDigest=meta['digest'],
            rpmSnapshot='fixture',rpmRepomdDigest='sha256:fixture',startedOn=now,finishedOn=now,builder='scanner-fixture')
(w/'image-metadata.json').write_text(json.dumps(meta))
(w/'resource-lock.json').write_text(json.dumps({'resources':[],'syntheticHarness':True}))
PY
private=$(mktemp -d)
trap 'rm -rf "${private}"' EXIT
export COSIGN_PASSWORD=harness-only COSIGN_KEY_PATH="${private}/cosign.key" COSIGN_PUBLIC_KEY="${private}/cosign.pub"
cosign generate-key-pair --output-key-prefix "${private}/cosign" >/dev/null
scripts/import_image.sh "${FACTORY_CATALOG_FILE}" "${work}"
scripts/sign_and_attest.sh "${FACTORY_CATALOG_FILE}" "${work}"
scripts/promote_image.sh "${FACTORY_CATALOG_FILE}" "${work}"
printf '{"realSyftGrype":true,"complianceAndProductTests":"synthetic","promoted":true}\n' >"${work}/scanner-smoke-result.json"
