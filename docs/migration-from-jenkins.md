# Migration from Jenkins

[Architecture](architecture.md) · [Configuration](configuration.md)

The stage scripts, catalog, policy, and evidence model are unchanged. What
changed is orchestration, trust boundaries, approvals, and the AI layer.

| Jenkins | Tekton / Pipelines-as-Code | Notes |
|---|---|---|
| `Jenkinsfile` | `.tekton/pipelines/image-build.yaml` + generated `<image>-on-*` PipelineRuns | one pipeline, parameterized per image |
| `Jenkinsfile.intake` | `.tekton/pipelines/intake.yaml`, `.tekton/tasks/factory-intake.yaml` | triggered by schedule via incoming webhook |
| `FACTORY_CHANGED_IMAGES` + plan waves | PaC `on-cel-expression` path filters per image; base releases fan out via `on-base-release` | apps build against the released base pointer |
| `FACTORY_ENABLE_*` boolean params | PipelineRun params (`publish`, `enable-*`) rendered by `factory/tekton.py` | per trigger, not per manual run |
| `runFactoryStage` (unstash → run → archive/stash) | `factory-stage` Task: `verify` → `run` (`onError: continue`) → `seal` | same "keep evidence on failure" semantics |
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

## Cut-over checklist

1. Run Tekton in parallel on a fork or a second Repository until PR results match
   Jenkins for all five catalog images.
2. Create Secrets from the existing Jenkins credentials (same scopes).
3. Point Artifactory permissions at the new ServiceAccount/workload identities.
4. Disable the Jenkins jobs, then merge this branch so PaC becomes authoritative.
5. Produce the first release requests for current production digests so
   `releases/` reflects reality.
