#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
query=${2:?yq query is required}
value=$(yq -er "${query}" "${catalog}")

if [[ "${value}" =~ ^\$\{([A-Z][A-Z0-9_]*)\}$ ]]; then
  variable=${BASH_REMATCH[1]}
  [[ -v ${variable} && -n ${!variable} ]] || {
    echo "${variable} is required by ${catalog}:${query}" >&2
    exit 2
  }
  value=${!variable}
fi

printf '%s\n' "${value}"
