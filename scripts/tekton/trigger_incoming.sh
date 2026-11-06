#!/usr/bin/env bash
# Trigger Pipelines-as-Code PipelineRuns through the incoming webhook.
# Used by the scheduler CronJobs and by the release pipeline to rebuild images
# that depend on a newly released base.
#
# Usage: trigger_incoming.sh <pipelinerun-name>...
# Env:   FACTORY_PAC_CONTROLLER_URL, FACTORY_PAC_REPOSITORY, FACTORY_DEFAULT_BRANCH,
#        FACTORY_PAC_INCOMING_SECRET
set -euo pipefail

: "${FACTORY_PAC_CONTROLLER_URL:?}"
: "${FACTORY_PAC_REPOSITORY:?Repository CR name}"
: "${FACTORY_PAC_INCOMING_SECRET:?}"
branch=${FACTORY_DEFAULT_BRANCH:-main}
(($# > 0)) || { echo "usage: trigger_incoming.sh <pipelinerun>..." >&2; exit 2; }
for pipelinerun in "$@"; do
  [[ ${pipelinerun} =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { echo "invalid PipelineRun: ${pipelinerun}" >&2; exit 2; }
  # The secret travels in the query string per the PaC incoming API; keep it out
  # of logs by passing the URL through a curl config on stdin.
  printf 'url = "%s/incoming?repository=%s&branch=%s&pipelinerun=%s&secret=%s"\n' \
    "${FACTORY_PAC_CONTROLLER_URL%/}" "${FACTORY_PAC_REPOSITORY}" "${branch}" \
    "${pipelinerun}" "${FACTORY_PAC_INCOMING_SECRET}" |
    curl --fail --silent --show-error --request POST --config - >/dev/null
  echo "triggered ${pipelinerun} on ${branch}"
done
