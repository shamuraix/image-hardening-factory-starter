#!/usr/bin/env bash
# Run a command in a throwaway runner pod with a harness PipelineRun's shared
# workspace mounted read-only at /workspace, for example to inspect evidence or
# a core file after a failure. Standard input is passed through, so a script
# can be piped in:
#   tests/integration/kind/workspace-exec.sh harness-build-abcde \
#     python3 - /workspace/source/core < tests/integration/kind/core-info.py
# The PipelineRun's volume exists until the PipelineRun is deleted.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
state=${FACTORY_HARNESS_STATE:-.local-factory/kind-review}
run=${1:?PipelineRun name is required}
shift
(($#)) || { echo 'a command is required' >&2; exit 2; }
k=(kubectl --kubeconfig "${state}/kubeconfig" -n factory-harness)
# Tekton creates the volumeClaimTemplate claim owned by the PipelineRun.
claim=$("${k[@]}" get pvc -o json | python3 -c '
import json, sys
run = sys.argv[1]
for item in json.load(sys.stdin)["items"]:
    if any(o.get("name") == run for o in item["metadata"].get("ownerReferences", [])):
        print(item["metadata"]["name"]); break
' "${run}")
[[ -n ${claim} ]] || { echo "no workspace volume found for ${run}" >&2; exit 1; }
pod="workspace-exec-$(python3 -c 'import secrets; print(secrets.token_hex(3))')"
cleanup() { "${k[@]}" delete pod "${pod}" --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT
python3 - "${pod}" "${claim}" <<'PYTHON' | "${k[@]}" apply -f - >/dev/null
import json, sys
pod, claim = sys.argv[1:3]
print(json.dumps({
    "apiVersion": "v1", "kind": "Pod",
    "metadata": {"name": pod, "labels": {"app.kubernetes.io/part-of": "image-hardening-factory"}},
    "spec": {
        "restartPolicy": "Never", "automountServiceAccountToken": False,
        "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "runAsGroup": 10001, "fsGroup": 10001},
        "volumes": [{"name": "workspace", "persistentVolumeClaim": {"claimName": claim, "readOnly": True}}],
        "containers": [{
            "name": "runner", "image": "localhost/factory-review-runner:review",
            "command": ["sleep", "1800"],
            "volumeMounts": [{"name": "workspace", "mountPath": "/workspace", "readOnly": True}],
            "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]},
                                "seccompProfile": {"type": "RuntimeDefault"}},
        }],
    },
}))
PYTHON
"${k[@]}" wait --for=condition=Ready "pod/${pod}" --timeout=120s >/dev/null
"${k[@]}" exec -i "${pod}" -- "$@"
