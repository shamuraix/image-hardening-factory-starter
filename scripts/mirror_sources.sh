#!/usr/bin/env bash
set -euo pipefail

: "${INTERNAL_GIT_BASE_URL:?}"
: "${SCM_MIRROR_USERNAME:?}"
: "${SCM_MIRROR_TOKEN:?}"

directory=$(mktemp -d)
trap 'rm -rf "${directory}"' EXIT
askpass="${directory}/git-askpass"
cat >"${askpass}" <<'EOF'
#!/usr/bin/env bash
case "${1}" in
  *Username*) printf '%s\n' "${SCM_MIRROR_USERNAME}" ;;
  *) printf '%s\n' "${SCM_MIRROR_TOKEN}" ;;
esac
EOF
chmod 0700 "${askpass}"

for catalog in catalog/images/*.yaml; do
  upstream=$(yq -r '.source.upstream' "${catalog}")
  mirror_path=$(yq -r '.source.mirrorPath' "${catalog}")
  revision=$(yq -r '.source.revision' "${catalog}")
  git clone --mirror "${upstream}" "${directory}/source.git"
  git -C "${directory}/source.git" cat-file -e "${revision}^{commit}"
  target="${INTERNAL_GIT_BASE_URL%/}/${mirror_path}.git"
  GIT_ASKPASS="${askpass}" GIT_TERMINAL_PROMPT=0 \
    git -C "${directory}/source.git" push --mirror "${target}"
  rm -rf "${directory}/source.git"
done
