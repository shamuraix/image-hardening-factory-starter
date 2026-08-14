#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
plan="${work_dir}/evidence/remediation/plan.json"
: "${GITLAB_REMEDIATION_BROKER_TOKEN:?}"
jq -e . "${plan}" >/dev/null

image=$(yq -r '.metadata.name' "${catalog}")
branch="remediate/${image}/${CI_PIPELINE_ID}"
git switch --create "${branch}"

# The broker accepts a patch only from the dedicated agent artifact. The
# read-only summary phase normally produces no patch, so this fails closed.
patch="${work_dir}/evidence/remediation/change.patch"
[[ -s "${patch}" ]] || { echo "agent did not produce a patch proposal" >&2; exit 1; }
git apply --check "${patch}"
git apply "${patch}"

changed=$(git diff --name-only)
if grep -Eq '^(policies/|compliance/|components/|scripts/(sign|promote|import)|.*vex|.*exception)' <<<"${changed}"; then
  echo "agent patch touches a protected path" >&2
  exit 1
fi
if grep -Ev '^(overlays/|catalog/images/|tests/profiles/)' <<<"${changed}" | grep -q .; then
  echo "agent patch contains an unapproved path" >&2
  exit 1
fi

git add overlays catalog/images tests/profiles
git commit -m "fix(${image}): propose verified vulnerability remediation"
git push "https://oauth2:${GITLAB_REMEDIATION_BROKER_TOKEN}@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git" HEAD:"${branch}"

curl --fail --silent --show-error --request POST \
  --header "PRIVATE-TOKEN: ${GITLAB_REMEDIATION_BROKER_TOKEN}" \
  --data-urlencode "source_branch=${branch}" \
  --data-urlencode "target_branch=${CI_DEFAULT_BRANCH}" \
  --data-urlencode "title=Remediate ${image} findings" \
  --data-urlencode "description=Agent-generated proposal. Clean rebuild, rescan, SBOM diff, policy evaluation and human approval are required. Pipeline: ${CI_PIPELINE_URL}" \
  "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/merge_requests" >/dev/null
