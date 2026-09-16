# Review remediation and acceptance status

## Ready for code review and commit

The original findings are recorded against baseline `cf4d1e1` in
[the review](2026-09-15-code-review.md). Remediation changes are supported by unit,
policy and real Jenkins/registry tests. They are ready for source review;
production rollout has the separate prerequisites below.

## Immediate change: FCS preference with Syft/Grype fallback

Jenkins selects FCS only when `FACTORY_ENABLE_FCS` is enabled and these settings
are present: `FACTORY_K8S_FCS_POD_TEMPLATE`, `FACTORY_FCS_RUNNER_IMAGE`,
`FALCON_REGION`, both Falcon credential IDs, and `FACTORY_FCS_REPORT_SCHEMA`.
Otherwise it schedules the authoritative Syft/Grype assessment for the gate.
A failure after selecting a backend never switches to another scanner.

Grype validates the SBOM's image identity and content hashes, the scanner report,
database validity, and KEV freshness. OPA enforces the catalog's database age,
critical/fixable-high/new-high/known-exploited thresholds and rejects unknown
severity. Malformed authoritative findings are rejected rather than replaced
with an empty informational report. Compliance and product-test results remain
independently blocking. Signed gate evidence identifies the selected backend;
release verification requires that backend's approving assessment for the digest.

## Remediation progress — 2026-09-15

| Finding | Implemented behavior |
| --- | --- |
| R1 RPM errors | Preserve exit status/stderr and reject operational failures, including allowlisted-looking output. |
| R2 gate status | A denied gate retains overall FAILURE while remediation can continue. |
| R3 cancellation | Optional-stage handling does not catch user interruptions. |
| R4 intake status | Check snapshot producer status before reading its output. |
| R5 diagnostics | Archive available reports on blocking failure without replacing the original exception. |
| R6 scanner caches | Export offline cache settings to scanner subprocesses. |
| R7 stage inputs | Supply prepare/scan/gate/assessment/provenance artifacts to their consumers. |
| R8 catalog paths | Use configured catalog directories in affected helpers. |
| R9 FCS reports | Require a reviewed, locally bundled JSON Schema; reject error envelopes and unconstrained schemas. Vendor qualification remains pending. |
| R10 registry auth | Create private, valid JSON authfiles and send passwords on stdin. |
| R11 promotion | Use ORAS 1.3 source/destination auth flags, explicit TLS, and verified retry behavior. |

### Baseline contract

An optional `FACTORY_GRYPE_BASELINE` must have an adjacent `.sig`, verified with
`FACTORY_BASELINE_PUBLIC_KEY`. The signed JSON requires `approved:true`, the same
image name, a valid `imageDigest`, and findings with `correlationKey` values.
Keys combine vulnerability ID, component and installed version. Without a baseline,
findings are treated as new. The status records the baseline and KEV hashes.

## Verified behavior

- Jenkins build 5 on `factory-proc-fixed`: SUCCESS, including real BuildKit
  RUN/export, Podman execution, failing-RUN rejection/cleanup, registry signing
  and promotion/retry, and handoff to a fresh pod.
- Final checks: 98 unit tests, 12 OPA tests and five catalog validations passed,
  along with Ruff, shell syntax and staged whitespace checks. The updated Jenkins
  startup script compiled against the installed plugins; restart behavior was
  reviewed but was not separately exercised by restarting the controller.
- Separate real Jenkins jobs: gate FAILURE, blocking-stage FAILURE, cancellation
  ABORTED, with diagnostics retained.
- Real offline Syft/Grype fixture: scan, gate, import, signing and promotion.
- Signed image missing required attestations: release verification rejects it.

Final Jenkins artifacts are saved in `.local-factory/proc-fixed-kind/results/5/`.
Stage-check results are in `.local-factory/proc-fixed-kind/stage-checks/`, and the
real scanner result is in `.local-factory/kind-review/scanner-smoke/`. Scanner
smoke compliance/product predicates are synthetic.

Logs and credentials remain in ignored `.local-factory/` state. See
[the supported harness setup](../local-kubernetes-testing.md) for crun, D-Bus,
pod namespaces and subordinate-ID requirements. Runtime experiments that did not
work are not supported setup paths.

## Remaining production acceptance work

1. **FCS vendor contract:** obtain the pinned CLI's reviewed native schema and
   sanitized passing/denied/error reports, then test against the licensed tenant.
   Merely configuring a schema does not establish vendor compatibility.
2. **Production runner parity:** rebuild the UBI runner with Grype 0.118.0 and
   a compatible signed offline database/KEV bundle. Verify Python 3.12 installation
   matches the `python3` entrypoint, mapping helpers, security context and tool pins.
3. **Full production pipeline:** run the actual SCM/intake/build/release workflow
   with signed RPM snapshots, real compliance tailoring, application artifacts,
   product fixtures, and production credential separation. Synthetic fixture
   predicates do not qualify those controls.
4. **Deployment controls:** verify Artifactory authentication/immutability/referrers,
   offline network enforcement, approval/RBAC and any FIPS requirements.
5. **Portability:** native Rancher Desktop/arm64 execution remains untested.
6. **Development cache:** local application builds still reuse an existing base
   archive by filename. Rebuild/remove it when base inputs change; input-keyed
   invalidation is follow-up work, not a production release path.
