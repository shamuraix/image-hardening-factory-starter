#!/usr/bin/env bash
# Caller owns cleanup of this private directory; never create an empty authfile.
factory_registry_auth() {
  local directory=${1:?private auth directory is required}
  mkdir -p "${directory}"
  chmod 700 "${directory}"
  printf '{"auths":{}}\n' >"${directory}/config.json"
  chmod 600 "${directory}/config.json"
}
