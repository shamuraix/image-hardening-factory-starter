#!/usr/bin/env bash
set -euo pipefail

catalog=${1:?catalog file is required}
work_dir=${2:?work directory is required}
plan="${work_dir}/evidence/remediation/plan.json"
: "${SCM_REPOSITORY_URL:?}"
: "${SCM_REMEDIATION_USERNAME:?}"
: "${SCM_REMEDIATION_TOKEN:?}"
: "${SCM_REMEDIATION_AUTHOR_NAME:?}"
: "${SCM_REMEDIATION_AUTHOR_EMAIL:?}"
jq -e . "${plan}" >/dev/null

image=$(yq -r '.metadata.name' "${catalog}")
build_id=${FACTORY_BUILD_ID:?FACTORY_BUILD_ID is required}
[[ "${build_id}" =~ ^[A-Za-z0-9_.-]+$ ]] || {
  echo "FACTORY_BUILD_ID contains characters that are invalid in a branch name" >&2
  exit 2
}
branch="remediate/${image}/${build_id}"
git switch --create "${branch}"

# The broker accepts a patch only from the dedicated agent artifact. The
# read-only summary phase normally produces no patch, so this fails closed.
patch="${work_dir}/evidence/remediation/change.patch"
[[ -s "${patch}" ]] || { echo "agent did not produce a patch proposal" >&2; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo "broker checkout must be clean" >&2; exit 1; }
git apply --check --index "${patch}"
git apply --index "${patch}"
# Include newly added files in validation and prevent catalog policy changes.
scripts/validate_remediation.py

git config user.name "${SCM_REMEDIATION_AUTHOR_NAME}"
git config user.email "${SCM_REMEDIATION_AUTHOR_EMAIL}"
git commit -m "fix(${image}): propose verified vulnerability remediation"
askpass=$(mktemp)
trap 'rm -f "${askpass}"' EXIT
cat >"${askpass}" <<'EOF'
#!/usr/bin/env bash
case "${1}" in
  *Username*) printf '%s\n' "${SCM_REMEDIATION_USERNAME}" ;;
  *) printf '%s\n' "${SCM_REMEDIATION_TOKEN}" ;;
esac
EOF
chmod 0700 "${askpass}"
GIT_ASKPASS="${askpass}" GIT_TERMINAL_PROMPT=0 \
  git push "${SCM_REPOSITORY_URL}" HEAD:"${branch}"
printf 'REMEDIATION_BRANCH=%s\n' "${branch}" \
  >"${work_dir}/evidence/remediation/branch.env"
