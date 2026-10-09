# Migration from Jenkins

[Architecture](architecture.md) · [Configuration](configuration.md)

This page is for teams moving from an earlier Jenkins-based version of this
factory. No Jenkins files remain in this repository; the table maps each old
concept to its Tekton (pipeline engine) and Pipelines-as-Code (PaC, Git-event
trigger) replacement. The catalog, policy, and evidence model carried over;
orchestration, trust boundaries, approvals, scanner data handling, and the AI
layer changed.

| Jenkins | Tekton / Pipelines-as-Code | Notes |
|---|---|---|
| `Jenkinsfile` | `.tekton/pipelines/image-build.yaml` + generated `<image>-on-*` PipelineRuns | one pipeline, parameterized per image |
| `Jenkinsfile.intake` | `.tekton/pipelines/intake.yaml`, `.tekton/tasks/factory-intake.yaml` | triggered by schedule via incoming webhook |
| `FACTORY_CHANGED_IMAGES` + plan waves | PaC `on-cel-expression` path filters per image; base releases fan out via `on-base-release` | apps build against the released base pointer |
| `FACTORY_ENABLE_*` boolean params | PipelineRun params (`publish`, `enable-*`) rendered by `factory/tekton.py` | per trigger, not per manual run |
| `runFactoryStage` (unstash → run → archive/stash) | `factory-stage` Task: `verify` → `run` (`onError: continue`) → `seal` | evidence is kept when a stage fails |
| `stash` / `unstash` | shared PVC + SHA-256 seal manifests whose digests travel as Task results | tamper-evident across tasks |
| `catchError` for non-blocking stages | `allow-failure: "true"` + `status` result | gate remains blocking |
| Pod templates `FACTORY_K8S_*_POD_TEMPLATE` | Task `securityContext` + per-task ServiceAccounts (`taskRunSpecs`) + NetworkPolicies on `tekton.dev/pipelineTask` | trust classes preserved |
| `toolchain/jenkins-buildkit-pod.yaml` | `factory-rootless-build` Task (relaxed only on its run step) | |
| Credential IDs (`*_CREDENTIAL_ID`) | fixed Secret names, bound per step via `secretKeyRef` or step-scoped volumes | `deploy/secrets.example.yaml` |
| Branch check `isProtectedBranch()` | PR runs omit publish params and protected secrets; `pipelinerun_provenance: default_branch`; admission policy | branch checks in scripts were never a boundary |
| Lockable Resources for import/promotion | digest-unique quarantine tags, one release request per merge, PaC `concurrency_limit` | |
| `input` step for Gov approval | merge of a CODEOWNERS-reviewed `releases/<env>/<image>.yaml`; merger recorded as approver | auditable in git |
| Sign + promote in the same build | separate `factory-image-release` PipelineRun, evidence fetched by pinned referrer digest | |
| `archiveArtifacts` | evidence on the run PVC; evidence bundle attached to the quarantined image as an OCI referrer | |
| `scripts/ai_remediation.py` (single OpenAI-style call) | six Claude Code personas, headless and sandboxed, with a fresh-clone change broker | see [agents.md](agents.md) |
| `scripts/publish_remediation_branch.sh` | `scripts/agents/publish_change.sh` (runs from a fresh clone) | |
| `scripts/render_jenkins_plan.sh` | `scripts/render_plan.sh` | used by intake |
| — | Tekton Chains SLSA provenance and image signatures | additional, controller-held key |
| — | `releases/<base>/current.json` written by promotion | previously never written |
| Scanner data baked into the runner image | daily signed security-data bundle, fetched and verified by the prepare stage | see [security-data/README.md](../security-data/README.md) |

## Cut-over checklist

1. Run the Tekton factory in parallel (a fork, or a second PaC Repository) until
   pull-request results match the old system for all five catalog images.
2. Create the Secrets in `deploy/secrets.example.yaml` from the existing
   credentials, keeping the same scopes.
3. Point Artifactory permissions at the new ServiceAccounts or workload
   identities.
4. Run `security-data-on-schedule` and `intake-on-schedule` once, so the bundle
   pointer and signed locks exist before the first build.
5. Disable the old jobs, then make PaC authoritative on the default branch.
6. Produce release requests for the current production digests so `releases/`
   reflects reality.
