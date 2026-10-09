#!/usr/bin/env bash
# Assemble dist/ for the runner images: pinned tools (toolchain/download-tools.py)
# and a wheel of this repository with its dependencies, resolved for the runner's
# Python (3.12 on UBI 9 minimal). Needs network access to GitHub releases and
# PyPI; run it on a connected host, then build the Containerfiles offline.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
arch=${FACTORY_TOOLCHAIN_ARCH:-amd64}
dist=${1:-dist}
case "${arch}" in
  amd64) platform=manylinux2014_x86_64 ;;
  arm64) platform=manylinux2014_aarch64 ;;
  *) echo "FACTORY_TOOLCHAIN_ARCH must be amd64 or arm64" >&2; exit 2 ;;
esac

python3 toolchain/download-tools.py "${dist}" --arch "${arch}" ${FACTORY_TOOLCHAIN_WITH_CLAUDE:+--with-claude}

# Extra CA certificates for the runner image. Set FACTORY_CA_BUNDLE to a PEM file
# (for example the root of a TLS-inspecting proxy such as Zscaler) when image
# builds must trust it; the Containerfile adds it to the system trust store.
rm -rf "${dist}/ca-trust"
mkdir -p "${dist}/ca-trust"
if [[ -n ${FACTORY_CA_BUNDLE:-} ]]; then
  [[ -s ${FACTORY_CA_BUNDLE} ]] || { echo "FACTORY_CA_BUNDLE ${FACTORY_CA_BUNDLE} is not readable" >&2; exit 2; }
  grep -q 'BEGIN CERTIFICATE' "${FACTORY_CA_BUNDLE}" || { echo "FACTORY_CA_BUNDLE must be a PEM certificate bundle" >&2; exit 2; }
  cp "${FACTORY_CA_BUNDLE}" "${dist}/ca-trust/extra-ca.crt"
  echo "runner image will trust the extra CA bundle from ${FACTORY_CA_BUNDLE}"
fi

rm -rf "${dist}/factory-wheel"
mkdir -p "${dist}/factory-wheel"
python3 -m pip wheel --quiet --no-deps --wheel-dir "${dist}/factory-wheel" .
# Dependencies as prebuilt wheels for the runner's platform, not the host's.
python3 -m pip download --quiet --only-binary=:all: --platform "${platform}" \
  --python-version 3.12 --implementation cp --dest "${dist}/factory-wheel" \
  'PyYAML>=6.0,<7' 'jsonschema>=4.23,<5' 'ruff>=0.6,<1'
echo "toolchain assembled in ${dist}/ for ${arch}"
