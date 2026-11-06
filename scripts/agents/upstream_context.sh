#!/usr/bin/env bash
# Upstream reconnaissance for the upstream-sync agent (intake-read trust class).
# For each selected Iron Bank image, compare the pinned Repo One revision with
# the current upstream branch head and stage everything the agent needs to
# rebase the factory overlays offline. Argument: Tekton result file for the
# comma-separated list of drifted images.
set -euo pipefail

result=${1:?result file is required}
: "${FACTORY_AGENT_ROOT:?}"
: "${FACTORY_UPSTREAM_BRANCH:?FACTORY_UPSTREAM_BRANCH (Repo One branch to track) is required}"
images=${FACTORY_SYNC_IMAGES:-bitbucket-lts,confluence-lts,jira-lts}
catalog_dir=${FACTORY_CATALOG_DIR:-catalog/images}
workspace=${PWD}
context="${FACTORY_AGENT_ROOT}/context/upstream"
rm -rf "${context}"
mkdir -p "${context}"
export GIT_TERMINAL_PROMPT=0

drifted=()
IFS=',' read -r -a selected <<<"${images}"
for image in "${selected[@]}"; do
  [[ ${image} =~ ^[a-z0-9][a-z0-9-]+$ ]] || { echo "invalid image: ${image}" >&2; exit 2; }
  catalog="${catalog_dir}/${image}.yaml"
  upstream=$(yq -er '.source.upstream' "${catalog}")
  pinned=$(yq -er '.source.revision' "${catalog}")
  overlay=$(yq -r '.source.overlay // ""' "${catalog}")
  containerfile=$(yq -er '.source.containerfile' "${catalog}")
  manifest=$(yq -er '.source.hardeningManifest' "${catalog}")
  out="${context}/${image}"
  mkdir -p "${out}"
  head=$(git ls-remote --exit-code "${upstream}" "refs/heads/${FACTORY_UPSTREAM_BRANCH}" | awk 'NR == 1 {print $1}')
  [[ ${head} =~ ^[0-9a-f]{40}$ ]] || { echo "cannot resolve ${FACTORY_UPSTREAM_BRANCH} for ${image}" >&2; exit 1; }
  if [[ ${head} == "${pinned}" ]]; then
    jq -n --arg image "${image}" --arg revision "${pinned}" \
      '{image:$image,status:"in-sync",pinnedRevision:$revision,headRevision:$revision}' >"${out}/summary.json"
    continue
  fi
  drifted+=("${image}")
  source_dir="${out}/source"
  git init --quiet "${source_dir}"
  git -C "${source_dir}" fetch --quiet --no-tags --depth 1 "${upstream}" "${head}" "${pinned}"
  git -C "${source_dir}" diff --stat "${pinned}" "${head}" >"${out}/diffstat.txt"
  git -C "${source_dir}" diff "${pinned}" "${head}" -- "${manifest}" "${containerfile}" >"${out}/key-files.diff"
  git -C "${source_dir}" diff "${pinned}" "${head}" | head -c 400000 >"${out}/full.diff"
  git -C "${source_dir}" show --no-patch --format='%H%n%an <%ae>%n%cI%n%n%B' "${head}" >"${out}/head-commit.txt"
  git -C "${source_dir}" checkout --quiet --detach "${head}"
  yq -o=json '{"tags": .tags, "args": .args}' "${source_dir}/${manifest}" >"${out}/manifest-head.json"
  git -C "${source_dir}" show "${pinned}:${manifest}" | yq -o=json '{"tags": .tags, "args": .args}' \
    >"${out}/manifest-pinned.json"

  # Trial-apply the overlay series on a scratch worktree so the agent knows
  # exactly which patches still apply, and leave source/ clean at the new head.
  results='[]'
  if [[ -n ${overlay} ]]; then
    trial="${out}/trial"
    git -C "${source_dir}" worktree add --quiet --detach "${trial}" "${head}"
    while IFS= read -r -d '' patch; do
      if message=$(git -C "${trial}" apply --3way "${workspace}/${patch}" 2>&1); then
        applies=true
      else
        applies=false
        git -C "${trial}" reset --quiet --hard "${head}"
      fi
      results=$(jq -c --arg patch "${patch}" --argjson applies "${applies}" --arg message "${message}" \
        '. + [{patch:$patch,applies:$applies,message:($message|.[0:2000])}]' <<<"${results}")
    done < <(find "${overlay}" -type f -name '*.patch' -print0 | sort -z)
    git -C "${source_dir}" worktree remove --force "${trial}"
  fi
  jq -n --arg image "${image}" --arg upstream "${upstream}" --arg branch "${FACTORY_UPSTREAM_BRANCH}" \
    --arg pinned "${pinned}" --arg head "${head}" --arg productVersion "$(yq -r '.product.version' "${catalog}")" \
    --argjson overlay "${results}" --arg sourceDir "${source_dir}" \
    '{image:$image,status:"drifted",upstream:$upstream,branch:$branch,pinnedRevision:$pinned,
      headRevision:$head,catalogProductVersion:$productVersion,overlayTrial:$overlay,
      upstreamCheckout:$sourceDir}' >"${out}/summary.json"
done

jq -s '.' "${context}"/*/summary.json >"${context}/summary.json"
(IFS=','; printf '%s' "${drifted[*]}") >"${result}"
jq -r '.[] | "\(.image): \(.status)"' "${context}/summary.json"
