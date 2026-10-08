#!/usr/bin/env bash
# Run one agent persona headlessly. Invoked by the `agent` step of
# .tekton/tasks/factory-claude-agent.yaml, inside the agent image.
#
# The argv is built by factory.agents.build_command (unit tested) and always
# includes --bare, --permission-mode dontAsk, --permission-prompts none, turn
# and budget caps, and a JSON schema for the final report.
set -euo pipefail

: "${FACTORY_AGENT:?}"
: "${FACTORY_AGENT_ROOT:?}"
: "${ANTHROPIC_BASE_URL:?ANTHROPIC_BASE_URL must point at the approved model gateway}"
: "${ANTHROPIC_AUTH_TOKEN:?}"
source_root=${FACTORY_SHARED_SOURCE:-${PWD}}
root=${FACTORY_AGENT_ROOT}
repo="${root}/repo"
[[ -d ${repo}/.git ]] || { echo "agent working clone is missing; did the context step run?" >&2; exit 1; }

PYTHONPATH="${source_root}" python3 -m factory.cli agent-command \
  --agent "${FACTORY_AGENT}" --root "${root}" --source "${source_root}" >"${root}/command.json"
mapfile -t argv < <(jq -r '.argv[]' "${root}/command.json")
claude --version >"${root}/claude-version.txt"

cd "${repo}"
set +e
timeout --signal=INT --kill-after=60s "${FACTORY_AGENT_TIMEOUT:-1500s}" \
  "${argv[@]}" >"${root}/result.json" 2>"${root}/agent.stderr.log"
status=$?
set -e
jq -r '"turns=\(.num_turns // "?") cost_usd=\(.total_cost_usd // "?") error=\(.is_error // "?")"' \
  "${root}/result.json" 2>/dev/null || true
tail -n 20 "${root}/agent.stderr.log" >&2 || true
exit "${status}"
