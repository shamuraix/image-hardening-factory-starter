#!/usr/bin/env bash
# PostToolUse: remind Claude to regenerate PaC PipelineRuns after catalog or
# renderer edits, so generated triggers never drift from the catalog.
set -euo pipefail
path=$(jq -r '.tool_input.file_path // ""')
case "${path}" in
  */catalog/images/*.yaml|catalog/images/*.yaml|*/factory/tekton.py|factory/tekton.py)
    jq -n '{hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:
      "Catalog or renderer changed: run `make tekton-render` and include the regenerated .tekton/*-on-*.yaml files."}}'
    ;;
esac
exit 0
