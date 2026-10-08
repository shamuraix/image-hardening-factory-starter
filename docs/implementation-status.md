# Implementation status

## Summary

The factory now runs on Tekton with Pipelines-as-Code triggers and a Claude Code
agent layer. Unit tests (145), Ruff, shellcheck on all scripts and inline step
scripts, and `tekton-lint` pass. Production readiness still depends on
environment-specific cluster, repository, credential, network, and gateway work,
and on a first end-to-end run in a real cluster.

## Status matrix

| Capability | Code status | Environment work remaining |
|---|---|---|
| Five-image catalog and schema | Implemented and tested | Change ownership/governance |
| PaC triggers generated from the catalog | Implemented; drift-tested | Install PaC app/webhook; branch protection |
| Build/evidence/gate pipeline (Tekton) | Implemented; invariants tested; harness-smoke for stage mechanics | First real-cluster run of rootless build/test stages |
| Stage sealing (stash replacement) | Implemented and tested (`artifacts.sh`) | — |
| Per-task trust classes | ServiceAccounts, NetworkPolicies, per-step secrets | Replace placeholder CIDRs; workload identity |
| PR secret isolation admission policy | Implemented | Confirm PaC event-type propagation to pods |
| Source mirroring and resource locks | Implemented and tested | Approved origins, retention |
| Rootless BuildKit OCI build | Implemented | Node user-namespace support |
| SBOM / scanner / compliance / tests | Implemented | Signed tool/data bundle lifecycle; FIPS node pool |
| OPA gate | Implemented | Policy change control |
| Quarantine import + evidence referrer | Implemented | Quarantine write scope |
| Release requests (merge = approval) | Implemented and tested | CODEOWNERS teams for Gov paths |
| Cosign signing/attestation | Implemented | Environment keys (KMS) and rotation |
| Promotion + release pointer + dependent fan-out | Implemented | Destination registry verification SLO |
| Tekton Chains provenance | Configured | Chains signing key (KMS), optional internal Rekor |
| Claude Code agents (6 personas) | Implemented; loading, commands, write policy, hook tested | Approved LLM gateway; agent image build; evaluate before widening |
| Optional Helmper/Copacetic/Hummingbird | Implemented (off by default) | Decide informational vs enforced |
| UBI 10 canary | Implemented | Vendor compatibility acceptance |

## Known gaps and follow-ups

- The kind harness exercises stage mechanics, not the rootless build stages,
  PaC, Chains, or agents (see `docs/local-kubernetes-testing.md`).
- `docs/Image-Hardening-Factory-Introduction-v1.1.pptx` still describes Jenkins.
- PaC native `settings.ai` analysis supports only OpenAI/Gemini providers; the
  factory uses Claude Code Tasks instead and does not configure it.

## Activation guidance

First production activation should stop after quarantine until evidence, policy,
signing, and promotion are independently reviewed, then release commercial
before Gov environments. Keep agents in propose-only mode and measure them
(`docs/agents.md`) before considering any wider authority.
