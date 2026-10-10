# Implementation status

[Project overview](../README.md) · [Architecture](architecture.md)

## Summary

The factory runs on Tekton, with Pipelines-as-Code (PaC) triggers and a Claude
Code agent layer. `make ci` runs catalog validation, the generated-PipelineRun
drift check, release-request validation, Ruff, and the unit tests (plus OPA
policy tests when `opa` is installed). Production readiness still depends on
environment-specific cluster, repository, credential, network, and gateway work,
and on a first end-to-end run in a real cluster. The local harness has run
end-to-end on a Rancher Desktop (k3s) cluster: checkout, validate, prepare,
rootless build, SBOM, rootless tests, seal verification, and the tamper probe
(`docs/local-kubernetes-testing.md`).

## Status matrix

| Capability | Code status | Environment work remaining |
|---|---|---|
| Five-image catalog and schema | Implemented and tested | Change ownership and governance |
| PaC triggers generated from the catalog | Implemented; drift-tested | GitLab webhook and token (Secret `factory-gitlab-webhook`); protected `main` with code-owner approval |
| Build, evidence, and gate pipeline (Tekton) | Implemented; invariants tested; the harness runs validate, prepare, build, SBOM, test, and sealing with the real Tasks and passes on a Rancher Desktop cluster | First run of the full pipeline (scan, gate, quarantine, release) on a real cluster |
| Stage sealing (tamper-evident handoff between tasks) | Implemented and tested (`scripts/tekton/artifacts.sh`) | — |
| Per-task trust classes (ServiceAccount, NetworkPolicy, and secrets per task) | Implemented | Replace placeholder CIDRs; workload identity |
| Merge-request secret isolation admission policy | Implemented as an allowlist (`push`, `incoming` only), so GitLab's `Merge Request` event type is covered | Confirm PaC event-type propagation to pods on the real cluster |
| Source mirroring and resource locks | Implemented and tested | Approved origins, retention |
| Rootless BuildKit OCI build | Implemented and proven in-cluster: build and test pods run with `hostUsers: false`, `procMount: Unmasked`, and only `SETUID`/`SETGID` in the run step's bounding set, with libcap-aware mapping helpers in the runner | Kubernetes 1.33+ (or both feature gates on), containerd 2.0+/CRI-O, kubelet `idsPerPod` ≥ 262144 |
| SBOM, scanners, compliance, tests | Implemented; Grype validation fails closed | FIPS node pool |
| Signed offline security-data bundle (daily) | Implemented and tested (`tests/unit/test_security_data.py`) | Intake runner image with scanner tools and SCAP content; egress to database mirrors and the CISA KEV feed |
| Signed vulnerability baselines | Verification implemented in `factory/grype.py` | No process yet to create, sign, and distribute baselines; without one every finding counts as new |
| OPA gate | Implemented | Policy change control |
| Quarantine import and evidence referrer | Implemented | Quarantine write scope |
| Release requests (merge = approval) | Implemented and tested | CODEOWNERS teams for Gov paths |
| Cosign signing and attestation | Implemented | Environment keys (KMS) and rotation |
| Promotion, release pointer, and rebuild of dependent images | Implemented | Destination registry verification SLO |
| Tekton Chains provenance | Configured (`slsa/v2alpha3`, SLSA v1.0) | Chains signing key (KMS); optional internal Rekor |
| Claude Code agents (6 personas) | Implemented; loading, commands, write policy, and hook tested | Approved LLM gateway; agent image build; evaluate before widening |
| UBI 10 canary | Implemented | Vendor compatibility acceptance |

## Known gaps and follow-ups

- The harness builds, SBOMs, and tests a base image with the real Tasks and
  passes on Rancher Desktop, but does not run scan, gate, quarantine, PaC,
  Chains, or the agents (see `docs/local-kubernetes-testing.md`). Those need
  Artifactory, the signed security-data bundle, credentials, and a gateway.
- On Apple silicon the harness builds `linux/amd64` images through Rosetta;
  it proves the pipeline mechanics, not amd64 runtime behaviour. Validate on
  x86-64 nodes before trusting a harness-built candidate's test results.
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
