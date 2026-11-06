# Implementation status

[Project overview](../README.md) · [Scope](scope.md) · [Architecture](architecture.md)

## Summary

The factory runs on Tekton, with Pipelines-as-Code (PaC) triggers and a Claude
Code agent layer. `make ci` runs catalog validation, the generated-PipelineRun
drift check, release-request validation, Ruff, and the unit tests (plus OPA
policy tests when `opa` is installed). Production readiness still depends on
environment-specific cluster, repository, credential, network, and gateway work,
and on a first end-to-end run in a real cluster.

The scope was narrowed to cloud-native stages on Kubernetes 1.28
([scope.md](scope.md)): the Podman test stage, the OpenSCAP compliance stage,
ClamAV, and the kind harness were removed. The earlier in-cluster proof of the
build stage used pod user namespaces on Kubernetes 1.33; the 1.28 build path
(no pod user namespace, file-capability mapping helpers, AppArmor annotation)
has not yet run in a cluster.

## Status matrix

| Capability | Code status | Environment work remaining |
|---|---|---|
| Five-image catalog and schema | Implemented and tested | Change ownership and governance |
| PaC triggers generated from the catalog | Implemented; drift-tested | GitLab webhook and token (Secret `factory-gitlab-webhook`); protected `main` with code-owner approval |
| Build, evidence, and gate pipeline (Tekton) | Implemented; invariants tested | First run of the full pipeline on a Kubernetes 1.28 cluster |
| Stage sealing (tamper-evident handoff between tasks) | Implemented and tested (`scripts/tekton/artifacts.sh`) | — |
| Per-task trust classes (ServiceAccount, NetworkPolicy, and secrets per task) | Implemented | Replace placeholder CIDRs; workload identity |
| Merge-request secret isolation admission policy | Implemented as an allowlist (`push`, `incoming` only), so GitLab's `Merge Request` event type is covered; `v1beta1` for Kubernetes 1.28 | Enable the ValidatingAdmissionPolicy feature gate and `v1beta1` API on the cluster; confirm PaC event-type propagation to pods |
| Source mirroring and resource locks | Implemented and tested | Approved origins, retention |
| Rootless BuildKit OCI build | Implemented for Kubernetes 1.28: no pod user namespace or unmasked `/proc`, `--oci-worker-no-process-sandbox`, only `SETUID`/`SETGID` in the run step's bounding set, libcap-aware mapping helpers with file capabilities, AppArmor via annotation; unit-tested | First in-cluster run on 1.28 with containerd 1.7 and the target node kernel; confirm the runner image keeps the helpers' file capabilities; dedicated build node pool |
| SBOM and scanners | Implemented; Grype validation fails closed | — |
| Signed offline security-data bundle (daily) | Implemented and tested (`tests/unit/test_security_data.py`) | Egress to database mirrors and the CISA KEV feed |
| Compliance scanning, malware scanning, runtime tests | Out of scope ([scope.md](scope.md)) | Owned by the consuming environment |
| Signed vulnerability baselines | Verification implemented in `factory/grype.py` | No process yet to create, sign, and distribute baselines; without one every finding counts as new |
| OPA gate | Implemented | Policy change control |
| Quarantine import and evidence referrer | Implemented | Quarantine write scope |
| Release requests (merge = approval) | Implemented and tested | CODEOWNERS teams for Gov paths |
| Cosign signing and attestation | Implemented | Environment keys (KMS) and rotation |
| Promotion, release pointer, and rebuild of dependent images | Implemented | Destination registry verification SLO |
| Tekton Chains provenance | Configured (`slsa/v2alpha3`, SLSA v1.0) | Chains signing key (KMS); optional internal Rekor |
| Claude Code agents (6 personas) | Implemented; loading, commands, write policy, and hook tested | Approved LLM gateway; agent image build; evaluate before widening |
| UBI 10 base for the Atlassian images | Implemented; overlays apply and context-check passes against the pinned Iron Bank sources | First builds on UBI 10; commercial release of `ubi10-minimal` to publish its pointer; confirm node CPU baseline |
| UBI 9 rollback base | Releasable, no catalog dependents; still the runner images' base | Decide how long to keep it; move the runner to UBI 10 before retiring it |
| Static checks (context-check, image-check) | Implemented and unit-tested; context-check run against the pinned Iron Bank sources | First image-check against real UBI 10 builds; review advisory parity findings |

## Known gaps and follow-ups

- The local harness (`docs/local-harness.md`) builds base and application
  images with the production Tasks on a 1.28+ cluster, but not scan, gate, or
  release; those still need a development cluster (`deploy/environments/dev`).
  Its first run is also the first run of the 1.28 build path.
- Without pod user namespaces, build pods use real host UIDs (10001 and the
  `100000:65536` subordinate range). Keep build pods on a dedicated node pool.
- The Pipelines-as-Code v0.51 and Tekton Chains v0.28 minimum Kubernetes
  versions have not been checked against 1.28.
- The KEV freshness check uses the date CISA last changed the catalog. A gap of
  more than 72 hours in CISA updates makes every scan fail; a human needs to
  decide whether to accept that risk or change the rule.
- PaC's built-in LLM analysis supports only OpenAI and Gemini
  ([PaC docs](https://pipelinesascode.com/docs/guides/llm-analysis/)). The
  factory uses its own Claude Code Tasks instead and does not configure it.

## Activation guidance

First production activation should stop after quarantine until evidence, policy,
signing, and promotion are independently reviewed. Then release to commercial
before the Gov environments. Keep agents in propose-only mode and measure them
(`docs/agents.md`) before considering any wider authority.
