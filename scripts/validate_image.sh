#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
image=$(yq -r '.metadata.name' "${catalog}")
source_revision=$(yq -r '.source.revision' "${catalog}")
containerfile=$(yq -r '.source.containerfile' "${catalog}")
mkdir -p "work/${image}"

[[ "${source_revision}" =~ ^[0-9a-f]{40}$ ]]
[[ "${containerfile}" != /* && "${containerfile}" != *..* ]]

if [[ $(yq -r '.metadata.track' "${catalog}") == release ]] && \
   [[ $(yq -r '.build.base.kind' "${catalog}") == catalog ]]; then
  base=$(yq -r '.build.base.image' "${catalog}")
  [[ $(yq -r '.metadata.track' "catalog/images/${base}.yaml") == release ]]
fi

jq -n \
  --arg image "${image}" \
  --arg revision "${source_revision}" \
  '{valid:true,image:$image,sourceRevision:$revision}' \
  >"work/${image}/validation.json"
