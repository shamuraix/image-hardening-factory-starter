# shellcheck shell=bash
# Minimal GitLab / GitHub change-request helpers for the change broker.
# Requires FACTORY_SOURCE_URL, FACTORY_GIT_PROVIDER, SCM_BOT_TOKEN.

scm_repo_path() {
  local url=${FACTORY_SOURCE_URL%.git}
  sed -E 's#^https?://[^/]+/##' <<<"${url}"
}

scm_host() {
  sed -E 's#^https?://([^/]+)/.*#\1#' <<<"${FACTORY_SOURCE_URL}"
}

# scm_open_change_request <head> <base> <title> <body-file> <draft:true|false>
scm_open_change_request() {
  local head=${1} base=${2} title=${3} body_file=${4} draft=${5}
  local path payload response
  path=$(scm_repo_path)
  case "${FACTORY_GIT_PROVIDER:-gitlab}" in
    github)
      local api=${FACTORY_GITHUB_API_URL:-https://api.github.com}
      payload=$(jq -n --arg title "${title}" --arg head "${head}" --arg base "${base}" \
        --rawfile body "${body_file}" --argjson draft "${draft}" \
        '{title:$title,head:$head,base:$base,body:$body,draft:$draft,maintainer_can_modify:false}')
      response=$(curl --fail --silent --show-error --request POST \
        --header "Authorization: Bearer ${SCM_BOT_TOKEN}" \
        --header "Accept: application/vnd.github+json" \
        --data "${payload}" "${api}/repos/${path}/pulls")
      local number
      number=$(jq -r '.number' <<<"${response}")
      curl --silent --show-error --request POST \
        --header "Authorization: Bearer ${SCM_BOT_TOKEN}" \
        --header "Accept: application/vnd.github+json" \
        --data '{"labels":["agent-proposed"]}' \
        "${api}/repos/${path}/issues/${number}/labels" >/dev/null || true
      jq -r '"opened " + .html_url' <<<"${response}"
      ;;
    gitlab)
      local api=${FACTORY_GITLAB_API_URL:-https://$(scm_host)/api/v4} project
      project=$(jq -rn --arg p "${path}" '$p|@uri')
      [[ ${draft} == true ]] && title="Draft: ${title}"
      payload=$(jq -n --arg title "${title}" --arg source "${head}" --arg target "${base}" \
        --rawfile description "${body_file}" \
        '{title:$title,source_branch:$source,target_branch:$target,description:$description,
          labels:"agent-proposed",remove_source_branch:true}')
      response=$(curl --fail --silent --show-error --request POST \
        --header "PRIVATE-TOKEN: ${SCM_BOT_TOKEN}" --header "Content-Type: application/json" \
        --data "${payload}" "${api}/projects/${project}/merge_requests")
      jq -r '"opened " + .web_url' <<<"${response}"
      ;;
    *)
      echo "unsupported git provider: ${FACTORY_GIT_PROVIDER}" >&2
      return 2
      ;;
  esac
}
