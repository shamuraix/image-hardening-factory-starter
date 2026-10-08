#!/usr/bin/env bash
# PreToolUse guard shared by interactive sessions (.claude/settings.json) and CI
# agent runs (agents/ci/settings.json).
#
# Interactive: asks before edits to trust-boundary files, denies edits to
# generated PipelineRuns and key material, denies destructive release commands.
# CI (FACTORY_AGENT set): denies any edit outside the persona's writable paths
# and commands that could reach the network or credentials.
#
# Defence in depth only: the change broker's fresh-clone validation is the
# authoritative boundary for agent output.
set -euo pipefail

input=$(cat)
tool=$(jq -r '.tool_name // ""' <<<"${input}")
project=${CLAUDE_PROJECT_DIR:-$(jq -r '.cwd // "."' <<<"${input}")}

decide() {
  jq -n --arg decision "${1}" --arg reason "${2}" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:$decision,permissionDecisionReason:$reason}}'
  exit 0
}

relative_path() {
  local path=${1}
  case "${path}" in
    "${project}"/*) printf '%s' "${path#"${project}"/}" ;;
    /*) printf '%s' "${path}" ;;
    *) printf '%s' "${path#./}" ;;
  esac
}

ci_writable() {
  local path=${1}
  [[ -n ${FACTORY_AGENT_ROOT:-} && ${path} == "${FACTORY_AGENT_ROOT}/context/"* ]] && return 0
  python3 - "${FACTORY_SHARED_SOURCE:-${project}}" "${FACTORY_AGENT}" "${path}" <<'PYTHON' && return 0
import sys
sys.path.insert(0, sys.argv[1])
from factory.agents import load_agent
agent = load_agent(sys.argv[1], sys.argv[2])
path = sys.argv[3]
raise SystemExit(0 if any(path == p or path.startswith(p) for p in agent.writable_paths) else 1)
PYTHON
  return 1
}

case "${tool}" in
  Edit|Write|MultiEdit|NotebookEdit)
    raw=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""' <<<"${input}")
    path=$(relative_path "${raw}")
    if [[ -n ${FACTORY_AGENT:-} ]]; then
      ci_writable "${path}" || decide deny "${FACTORY_AGENT} may not modify ${path}"
      exit 0
    fi
    case "${path}" in
      *.key|*.pem|*credentials*|.env|.env.*)
        decide deny "key material and credentials are never edited here" ;;
      .tekton/*-on-*.yaml)
        decide deny "generated PipelineRun: edit factory/tekton.py and run make tekton-render" ;;
      policies/*|.tekton/tasks/*|.tekton/pipelines/*|deploy/*|releases/*|CODEOWNERS|agents/ci/*|\
      scripts/sign_and_attest.sh|scripts/promote_image.sh|scripts/import_image.sh|\
      scripts/verify_release_evidence.sh|scripts/agents/publish_change.sh|scripts/tekton/artifacts.sh)
        decide ask "${path} is a trust-boundary file; confirm this change is intended" ;;
    esac
    ;;
  Bash)
    command=$(jq -r '.tool_input.command // ""' <<<"${input}")
    if [[ -n ${FACTORY_AGENT:-} ]]; then
      # Deliberately broad: any git invocation mentioning a network verb (even
      # after -C/-c options), network/credential tools, /proc, or env dumps.
      if grep -Eq '(^|[;&|(`[:space:]])(curl|wget|nc|ncat|socat|ssh|scp|cosign|skopeo|oras|kubectl|env|printenv)([[:space:]]|$)|/proc/|(^|[^[:alnum:]_-])git([[:space:]].*)?[[:space:]](push|remote|fetch|clone|ls-remote|send-email)([[:space:]]|$)|ANTHROPIC_|SCM_|TOKEN' <<<"${command}"; then
        decide deny "command is not permitted for factory agents"
      fi
      exit 0
    fi
    if grep -Eq 'cosign[[:space:]]+(sign|attest)|git[[:space:]]+push[[:space:]].*--force|kubectl[[:space:]]+delete' <<<"${command}"; then
      decide deny "signing, force-push, and cluster deletes are pipeline-only operations"
    fi
    ;;
esac
exit 0
