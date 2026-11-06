#!/usr/bin/env bash
# Change broker. Runs from a FRESH clone of the pinned commit (see the publish
# step of .tekton/tasks/factory-claude-agent.yaml), never from the shared
# workspace. Inputs from the agent directory are treated as data:
#   propose-change  : agent/change.patch -> re-validated -> draft change request
#   release-request : sealed build evidence -> releases/<env>/<image>.yaml ->
#                     change request whose merge triggers signing/promotion
# Nothing is ever merged here; branch protection and CODEOWNERS decide.
set -euo pipefail

: "${FACTORY_AGENT:?}"
: "${FACTORY_AGENT_MODE:?}"
: "${FACTORY_AGENT_ROOT:?}"
: "${FACTORY_IMAGE:?}"
: "${FACTORY_COMMIT_SHA:?}"
: "${FACTORY_SOURCE_URL:?}"
: "${FACTORY_BROKER_ROOT:?}"
: "${FACTORY_BROKER_ASKPASS:?}"
: "${SCM_BOT_TOKEN:?}"
cd "${FACTORY_BROKER_ROOT}"
export PYTHONPATH="${FACTORY_BROKER_ROOT}"
base=${FACTORY_DEFAULT_BRANCH:-main}
root=${FACTORY_AGENT_ROOT}
git config user.name "${SCM_BOT_AUTHOR_NAME:-image-factory-bot}"
git config user.email "${SCM_BOT_AUTHOR_EMAIL:-image-factory-bot@invalid}"
source scripts/agents/lib_scm.sh

body_file=$(mktemp)
{
  if [[ -s ${root}/report.md ]]; then
    head -c 50000 "${root}/report.md" | sed 's/@/@\xe2\x80\x8b/g'
  else
    printf '_The agent produced no report for this run._\n'
  fi
  printf '\n---\nPipelineRun: `%s` · factory commit `%s`\n' "${FACTORY_PIPELINERUN:-unknown}" "${FACTORY_COMMIT_SHA}"
} >"${body_file}"

case "${FACTORY_AGENT_MODE}" in
  propose-change)
    patch="${root}/change.patch"
    [[ -s ${patch} ]] || { echo "no proposal to publish"; exit 0; }
    short=$(sha256sum "${patch}" | cut -c1-12)
    branch="agent/${FACTORY_AGENT}/${FACTORY_IMAGE}/${short}"
    title="${FACTORY_AGENT}: proposed change for ${FACTORY_IMAGE}"
    draft=true
    git switch --quiet --create "${branch}"
    git apply --check --index "${patch}"
    git apply --index "${patch}"
    # Authoritative policy check, using this clone's code and persona policy.
    python3 -m factory.cli agent-check --agent "${FACTORY_AGENT}"
    python3 -m factory.cli validate --catalog "${FACTORY_CATALOG_DIR:-catalog/images}" >/dev/null
    git commit --quiet -m "${title}" \
      -m "Proposed by the ${FACTORY_AGENT} agent. Requires human review; a clean factory pipeline run is the only verification."
    ;;
  release-request)
    : "${FACTORY_QUARANTINE_SEAL:?release requests require the quarantine seal}"
    : "${FACTORY_SHARED_SOURCE:?}"
    environment=${FACTORY_RELEASE_ENV:-commercial}
    shared_work="${FACTORY_SHARED_SOURCE}/work/${FACTORY_IMAGE}"
    # Verify the evidence with this clone's verifier before using it.
    (
      cd "${FACTORY_SHARED_SOURCE}"
      FACTORY_WORK_DIR="work/${FACTORY_IMAGE}" \
        bash "${FACTORY_BROKER_ROOT}/scripts/tekton/artifacts.sh" verify "quarantine=${FACTORY_QUARANTINE_SEAL}"
    )
    digest=$(jq -er '.digest' "${shared_work}/import-result.json")
    version=$(yq -r '.product.version' "catalog/images/${FACTORY_IMAGE}.yaml")
    branch="release/${environment}/${FACTORY_IMAGE}/${digest:7:12}"
    title="release(${environment}): ${FACTORY_IMAGE} ${version} @ ${digest:0:19}"
    draft=false
    request="releases/${environment}/${FACTORY_IMAGE}.yaml"
    git switch --quiet --create "${branch}"
    python3 -m factory.cli release-request write --work-dir "${shared_work}" \
      --catalog-file "catalog/images/${FACTORY_IMAGE}.yaml" --environment "${environment}" \
      --pipeline-run "${FACTORY_PIPELINERUN:-}" --output "${request}" >/dev/null
    python3 -m factory.cli release-request validate "${request}" >/dev/null
    git add "${request}"
    git commit --quiet -m "${title}" -m "Merging signs and promotes ${digest}."
    {
      printf '## Release request\n\nMerging this change signs and promotes `%s` to **%s**.\n\n' "${digest}" "${environment}"
      printf 'Approvers: review the readiness summary and gate warnings below. The merge is the approval of record.\n\n'
      cat "${body_file}"
    } >"${body_file}.release"
    mv "${body_file}.release" "${body_file}"
    ;;
  *)
    echo "nothing to publish for mode ${FACTORY_AGENT_MODE}"
    exit 0
    ;;
esac

if GIT_ASKPASS="${FACTORY_BROKER_ASKPASS}" git ls-remote --exit-code --heads \
  "${FACTORY_SOURCE_URL}" "refs/heads/${branch}" >/dev/null 2>&1; then
  echo "branch ${branch} already exists; an identical proposal is open or was handled"
  exit 0
fi
GIT_ASKPASS="${FACTORY_BROKER_ASKPASS}" git push --quiet "${FACTORY_SOURCE_URL}" "HEAD:refs/heads/${branch}"
scm_open_change_request "${branch}" "${base}" "${title}" "${body_file}" "${draft}"
