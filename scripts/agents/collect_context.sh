#!/usr/bin/env bash
# Gather the read-only inputs an agent persona needs into agent/context and
# create the agent's private working clone. Runs before the agent with no
# credentials. Arguments are optional <pipelineTask>=<status> pairs.
set -euo pipefail

: "${FACTORY_AGENT:?}"
: "${FACTORY_AGENT_ROOT:?}"
: "${FACTORY_COMMIT_SHA:?}"
image=${FACTORY_IMAGE:-factory}
root=${FACTORY_AGENT_ROOT}
context="${root}/context"
mkdir -p "${context}"
max_log_lines=${FACTORY_AGENT_LOG_LINES:-400}
max_bytes=${FACTORY_AGENT_MAX_INPUT_BYTES:-400000}

# The persona prompt, CI settings, and schemas come from this checkout, so it
# must still be exactly the commit under test.
FACTORY_WORK_DIR="work/${image}" scripts/tekton/artifacts.sh verify-source "${FACTORY_COMMIT_SHA}"

rm -rf "${root}/repo"
git clone --quiet --no-hardlinks . "${root}/repo"
git -C "${root}/repo" checkout --quiet --detach "${FACTORY_COMMIT_SHA}"
git -C "${root}/repo" remote remove origin
git -C "${root}/repo" config user.name "factory-agent"
git -C "${root}/repo" config user.email "factory-agent@invalid"

statuses='{}'
for pair in "$@"; do
  [[ ${pair} == *=* ]] || continue
  statuses=$(jq -c --arg k "${pair%%=*}" --arg v "${pair#*=}" '. + {($k): $v}' <<<"${statuses}")
done
jq -n --arg agent "${FACTORY_AGENT}" --arg image "${image}" \
  --arg mode "${FACTORY_AGENT_MODE:-report}" --arg event "${FACTORY_EVENT_TYPE:-}" \
  --arg commit "${FACTORY_COMMIT_SHA}" --arg branch "${FACTORY_BRANCH_NAME:-}" \
  --arg change "${FACTORY_CHANGE_ID:-}" --argjson statuses "${statuses}" \
  '{agent:$agent,image:$image,mode:$mode,eventType:$event,commit:$commit,branch:$branch,changeId:$change,taskStatuses:$statuses}' \
  >"${context}/run.json"

copy_if() { [[ -f ${1} ]] && cp "${1}" "${context}/${2:-$(basename "${1}")}" || true; }
tail_logs() {
  local directory=${1} log
  mkdir -p "${context}/logs"
  for log in "${directory}"/logs/*.log; do
    [[ -f ${log} ]] || continue
    tail -n "${max_log_lines}" "${log}" >"${context}/logs/$(basename "${directory}")-$(basename "${log}")"
  done
}
evidence_summary() {
  local w=${1} e=${1}/evidence
  copy_if "${w}/validation.json"
  copy_if "${w}/image-metadata.json"
  copy_if "${e}/gate-result.json"
  copy_if "${e}/scans/delegated/status.json" assessment-status.json
  copy_if "${e}/database-status.json"
  copy_if "${e}/tests/result.json" test-result.json
  copy_if "${e}/compliance/result.json" compliance-result.json
  if [[ -f ${e}/findings.json ]]; then
    # Keep only what the gate can block on; full scanner output stays on disk.
    jq '{generatedAt, findings: [.findings[] | select(.severity == "CRITICAL" or .severity == "HIGH"
          or .severity == "UNKNOWN" or .knownExploited == true)], warnings}' \
      "${e}/findings.json" >"${context}/findings-blocking.json"
  fi
  if [[ -f ${e}/sbom.cdx.json ]]; then
    jq '[.components[]? | {name, version, purl, type}]' "${e}/sbom.cdx.json" \
      >"${context}/sbom-components.json"
  fi
}
image_exceptions() {
  jq --arg image "${1}" '.factory.exceptions.approved[$image] // []' \
    policies/exceptions/approved.json >"${context}/exceptions.json"
}

case "${FACTORY_AGENT}" in
  failure-triage)
    if [[ ${image} == factory ]]; then
      for directory in work/*/; do tail_logs "${directory%/}"; done
    else
      tail_logs "work/${image}"
      evidence_summary "work/${image}"
    fi
    copy_if "work/release-request.json"
    ;;
  cve-remediation)
    evidence_summary "work/${image}"
    image_exceptions "${image}"
    cp "catalog/images/${image}.yaml" "${context}/catalog.yaml"
    copy_if /opt/security-data/repository-candidates.json
    ;;
  release-readiness)
    evidence_summary "work/${image}"
    image_exceptions "${image}"
    copy_if "work/${image}/import-result.json"
    copy_if "work/${image}/evidence-bundle.json"
    if [[ -f work/${image}/resource-lock.json ]]; then
      jq '{source, resources: [.resources[] | {kind, filename, source, declaredDigest}]}' \
        "work/${image}/resource-lock.json" >"${context}/resource-lock-summary.json"
    fi
    previous="releases/${FACTORY_RELEASE_ENV:-commercial}/${image}.yaml"
    copy_if "${previous}" previous-release-request.yaml
    ;;
  exception-steward)
    cp policies/exceptions/approved.json "${context}/approved-exceptions.json"
    for catalog in catalog/images/*.yaml; do
      yq -o=json '{"image": .metadata.name, "product": .product, "revision": .source.revision}' \
        "${catalog}"
    done | jq -s . >"${context}/catalog-summary.json"
    ;;
  pipeline-reviewer)
    # factory-checkout fetched the target branch as the only remote-tracking ref.
    base=$(git for-each-ref --format='%(refname:short)' refs/remotes/origin | head -n1)
    if [[ -n ${base} ]] && git rev-parse --verify --quiet "${base}" >/dev/null &&
      merge_base=$(git merge-base "${base}" HEAD 2>/dev/null); then
      git diff --stat "${merge_base}" HEAD >"${context}/diffstat.txt"
      git diff "${merge_base}" HEAD | head -c "${max_bytes}" >"${context}/diff.patch"
    else
      git show --stat HEAD >"${context}/diffstat.txt"
      git show HEAD | head -c "${max_bytes}" >"${context}/diff.patch"
    fi
    copy_if docs/reviews/2026-09-15-code-review.md review-checklist.md
    ;;
  upstream-sync)
    [[ -d ${context}/upstream ]] || {
      echo "upstream context is missing; factory-upstream-context must run first" >&2
      exit 1
    }
    ;;
  *)
    echo "no context recipe for agent ${FACTORY_AGENT}" >&2
    exit 2
    ;;
esac
find "${context}" -type f \( -name '*.log' -o -name '*.patch' -o -name '*.txt' \) \
  -size +"${max_bytes}"c -exec truncate -s "${max_bytes}" {} +
find "${context}" -type f | sort
