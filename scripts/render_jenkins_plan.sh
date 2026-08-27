#!/usr/bin/env bash
set -euo pipefail

output=${1:?output path is required}

if [[ -n "${FACTORY_CHANGED_IMAGES:-}" ]]; then
  # Catalog names are schema-constrained; still reject whitespace and shell metacharacters.
  IFS=',' read -r -a changed <<<"${FACTORY_CHANGED_IMAGES}"
  for name in "${changed[@]}"; do
    [[ "${name}" =~ ^[a-z0-9][a-z0-9-]+$ ]] || { echo "invalid image name: ${name}" >&2; exit 2; }
  done
  PYTHONPATH=. python3 -m factory.cli plan \
    --catalog "${FACTORY_CATALOG_DIR:-catalog/images}" \
    --changed "${changed[@]}" \
    --output "${output}"
else
  PYTHONPATH=. python3 -m factory.cli plan \
    --catalog "${FACTORY_CATALOG_DIR:-catalog/images}" \
    --all \
    --output "${output}"
fi
