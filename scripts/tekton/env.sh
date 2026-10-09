# shellcheck shell=bash
# Source from a Tekton step after changing into the checked-out repository.
#
# Derives the shared factory environment (work directory, build identity,
# security-data location) from the identity values each Task passes in step env
# (see .tekton/tasks/*). Everything here is deterministic from those inputs.

: "${FACTORY_IMAGE:?FACTORY_IMAGE is required}"
[[ "${FACTORY_IMAGE}" =~ ^[a-z0-9][a-z0-9-]+$ ]] || {
  echo "invalid FACTORY_IMAGE: ${FACTORY_IMAGE}" >&2
  return 2 2>/dev/null || exit 2
}

factory_safe_name() {
  local value=${1:?value is required}
  printf '%s' "${value//[^A-Za-z0-9_.-]/-}" | cut -c1-120
}

export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
export FACTORY_WORKSPACE="${PWD}"
export FACTORY_CATALOG_DIR="${FACTORY_CATALOG_DIR:-catalog/images}"
export FACTORY_CATALOG_FILE="${FACTORY_CATALOG_DIR}/${FACTORY_IMAGE}.yaml"
export FACTORY_WORK_DIR="work/${FACTORY_IMAGE}"
export FACTORY_SCANNER_BACKEND="${FACTORY_SCANNER_BACKEND:-delegated-scanners}"
export FACTORY_RELEASE_ENV="${FACTORY_RELEASE_ENV:-commercial}"

# The PipelineRun name is unique per namespace and stable across every task of
# one run, so it replaces BUILD_NUMBER/JOB_NAME as the build identity.
: "${FACTORY_PIPELINERUN:?FACTORY_PIPELINERUN is required}"
FACTORY_BUILD_ID=$(factory_safe_name "${FACTORY_PIPELINERUN}-${FACTORY_COMMIT_SHA:0:12}")
export FACTORY_BUILD_ID
export FACTORY_BUILD_URL="${FACTORY_BUILD_URL:-tekton://${FACTORY_NAMESPACE:-unknown}/pipelineruns/${FACTORY_PIPELINERUN}}"
export FACTORY_RUNNER_ID="${FACTORY_TASKRUN:-unknown}"
FACTORY_JOB_ID=$(factory_safe_name "${FACTORY_BUILD_ID}-${FACTORY_IMAGE}")
export FACTORY_JOB_ID

# Verified security-data bundle fetched by the prepare stage; local runs fall
# back to a bundle unpacked at /opt/security-data.
if [[ -s ${PWD}/${FACTORY_WORK_DIR}/security-data/generated-at ]]; then
  export FACTORY_SECURITY_DATA="${PWD}/${FACTORY_WORK_DIR}/security-data"
else
  export FACTORY_SECURITY_DATA="${FACTORY_SECURITY_DATA:-/opt/security-data}"
fi

export HOME=/home/factory
export XDG_RUNTIME_DIR=/tmp/factory-runtime
export CONTAINERS_STORAGE_CONF=/home/factory/.config/containers/storage.conf
export STORAGE_DRIVER=vfs

mkdir -p "${FACTORY_WORK_DIR}/logs" "${XDG_RUNTIME_DIR}"
chmod 0700 "${XDG_RUNTIME_DIR}"
