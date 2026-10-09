#!/usr/bin/env bash
# Seal and verify stage outputs on the shared PipelineRun workspace.
#
# Tekton tasks in one PipelineRun share a workspace volume, so a later
# (possibly compromised) task could rewrite an earlier task's evidence. Each producing task therefore writes a
# manifest of SHA-256 digests and emits the manifest's own digest as a Tekton
# result. Results live in TaskRun status, which pods cannot rewrite, so every
# consumer re-derives the manifest digest from the volume and compares it to the
# result it was handed by the Pipeline before trusting any file.
#
# Usage:
#   artifacts.sh seal <name> <result-file> <glob>...
#   artifacts.sh verify [<name>=<sha256>]...
#   artifacts.sh verify-source <commit-sha>
set -euo pipefail
shopt -s globstar nullglob dotglob

seal_dir() {
  : "${FACTORY_WORK_DIR:?FACTORY_WORK_DIR is required; source scripts/tekton/env.sh}"
  printf '%s/.seals' "${FACTORY_WORK_DIR}"
}

valid_name() {
  [[ ${1} =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || { echo "invalid seal name: ${1}" >&2; exit 2; }
}

cmd_seal() {
  local name=${1:?seal name is required} result=${2:?result file is required}
  shift 2
  valid_name "${name}"
  local directory manifest
  directory=$(seal_dir)
  mkdir -p "${directory}"
  manifest="${directory}/${name}.sha256"
  local -a files=()
  local pattern path
  for pattern in "$@"; do
    [[ ${pattern} != /* && ${pattern} != *..* ]] || {
      echo "seal patterns must be repository-relative: ${pattern}" >&2
      exit 2
    }
    for path in ${pattern}; do
      [[ -f ${path} && ! -L ${path} && ${path} != */.seals/* ]] && files+=("${path}")
    done
  done
  : >"${manifest}.tmp"
  if ((${#files[@]} > 0)); then
    printf '%s\0' "${files[@]}" | sort -zu | xargs -0 sha256sum >"${manifest}.tmp"
  fi
  mv "${manifest}.tmp" "${manifest}"
  sha256sum "${manifest}" | awk '{printf "%s", $1}' >"${result}"
  printf 'sealed %s: %d file(s), manifest sha256:%s\n' \
    "${name}" "${#files[@]}" "$(cat "${result}")"
}

cmd_verify() {
  local entry name expected manifest observed directory
  directory=$(seal_dir)
  for entry in "$@"; do
    [[ -n ${entry} ]] || continue
    name=${entry%%=*}
    expected=${entry#*=}
    valid_name "${name}"
    [[ ${expected} =~ ^[0-9a-f]{64}$ ]] || {
      echo "seal ${name} has no valid digest (did the producing task run?)" >&2
      exit 1
    }
    manifest="${directory}/${name}.sha256"
    [[ -f ${manifest} && ! -L ${manifest} ]] || { echo "missing seal manifest: ${manifest}" >&2; exit 1; }
    observed=$(sha256sum "${manifest}" | awk '{print $1}')
    [[ ${observed} == "${expected}" ]] || {
      echo "seal ${name} manifest was modified: expected ${expected}, found ${observed}" >&2
      exit 1
    }
    if [[ -s ${manifest} ]]; then
      sha256sum --check --strict --quiet "${manifest}" || {
        echo "files sealed by ${name} were modified after sealing" >&2
        exit 1
      }
    fi
    printf 'verified %s\n' "${name}"
  done
}

cmd_verify_source() {
  local commit=${1:?commit sha is required}
  [[ ${commit} =~ ^[0-9a-f]{40}$ ]] || { echo "invalid commit: ${commit}" >&2; exit 2; }
  [[ $(git rev-parse HEAD) == "${commit}" ]] || {
    echo "workspace HEAD does not match the checked-out commit ${commit}" >&2
    exit 1
  }
  # Generated output lives under work/; everything else must match the commit.
  local dirty
  dirty=$(git status --porcelain --untracked-files=all -- . ':(exclude)work')
  [[ -z ${dirty} ]] || {
    printf 'repository checkout was modified by an earlier task:\n%s\n' "${dirty}" >&2
    exit 1
  }
}

command=${1:-}
shift || true
case "${command}" in
  seal) cmd_seal "$@" ;;
  verify) cmd_verify "$@" ;;
  verify-source) cmd_verify_source "$@" ;;
  *) echo "usage: artifacts.sh seal|verify|verify-source ..." >&2; exit 2 ;;
esac
