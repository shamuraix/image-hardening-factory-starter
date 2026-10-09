---
name: tekton-factory
description: Conventions for the factory's Tekton Tasks, Pipelines, and Pipelines-as-Code triggers — trust classes, sealing, credential placement, and generated PipelineRuns. Use when changing anything under .tekton/ or deploy/, or reviewing such a change.
---

# Tekton and Pipelines-as-Code conventions

## Layout

- `.tekton/tasks/*.yaml` and `.tekton/pipelines/*.yaml` — hand-written; PaC
  resolves `taskRef`/`pipelineRef` names to these files and embeds them.
- `.tekton/*-on-*.yaml` — **generated** by `factory/tekton.py`
  (`make tekton-render`); a unit test fails on drift. Never edit by hand.
- `deploy/` — cluster objects (Repository CR, ServiceAccounts, NetworkPolicies,
  Chains config, schedules, admission policy).

## Trust classes

Each pipeline task runs with a ServiceAccount (`taskRunSpecs` in the generated
PipelineRuns) and is selected by NetworkPolicy through the
`tekton.dev/pipelineTask` label.

| Class | Tasks | Secrets | Egress |
|---|---|---|---|
| offline | checkout, validate, sbom, scan, assessment, compliance, gate | none | git host (checkout only) |
| internal-read | prepare, release resolve, evidence context | Artifactory read, intake public key | Artifactory |
| buildkit / test | build / test | Artifactory read (build only) | Artifactory |
| import | quarantine | quarantine write | Artifactory |
| signing | attest | env Cosign key + referrer write | Artifactory |
| promotion | promote | release write, pointer write | Artifactory |
| intake | intake, security-data, upstream context | intake write and intake signing key (intake, security-data); mirror push (intake only) | approved upstreams |
| agent | triage, remediation, release-request, maintenance agents | LLM gateway; SCM bot (publish step, default branch only); PaC token (report step) | gateway, git host |

## Rules every change must keep

1. **Pull requests never get write, signing, release, or SCM-bot secrets.** The
   generated PR PipelineRuns omit them and `deploy/base/admission-policy.yaml`
   rejects pods that reference them for `pull_request` events.
2. **Definitions come from the default branch** (`pipelinerun_provenance:
   default_branch` on the Repository CR). Don't rely on branch checks in scripts.
3. **Secrets are per step**, via `secretKeyRef` env or a task volume mounted only
   in that step — never a workspace (workspaces are visible to every step).
4. **No workspace code next to credentials after untrusted code ran.** The agent
   task's publish and report steps are inline or run from a fresh clone.
5. **Verify, run, seal.** Stage tasks verify input seals and the source tree,
   run with `onError: continue` so evidence is kept, then seal and fail unless
   `allow-failure` is set. Prepare emits a second seal, `security-data`, that
   scan and compliance list as an input.
6. **Results for identity.** `IMAGE_URL`/`IMAGE_DIGEST` results let Tekton Chains
   sign and attest; never pass digests through files alone.
7. Pin images by digest; keep `securityContext` restricted except the single
   rootless BuildKit/Podman run step (which keeps `SETUID`/`SETGID` in its
   bounding set for the setuid mapping helpers and nothing else).
8. **Tasks never use `$(context.pipelineRun.*)`.** Tekton substitutes those
   only inside a Pipeline; in a Task they stay literal. Every Task takes a
   `pipelinerun` param and every Pipeline passes `$(context.pipelineRun.name)`;
   namespace comes from `$(context.taskRun.namespace)`. A unit test enforces it.
