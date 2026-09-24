# Operations guide

[Project overview](../README.md) · [Configuration](configuration.md) · [Architecture](architecture.md)

## Quick operations runbook

Use this section for first production activation and day-1 readiness checks.

### 1) Prepare core infrastructure

- Create Artifactory repositories for source, upstream OCI, quarantine, release,
  and canary flows.
- Configure Jenkins Kubernetes trust classes (`FACTORY_K8S_*_POD_TEMPLATE`).
- Configure runner image settings as digest-qualified references.
- Configure Jenkins credential ID settings and workload identity mapping.

### 2) Run intake and verify locks

- Run `Jenkinsfile.intake` in connected mode.
- Confirm signed resource locks are produced and stored under:
  `locks/<image>/<source-revision>/resource-lock.json` (+ `.sig`).

### 3) Run controlled release path

Enable only core stages first:

- `VALIDATE`, `PREPARE`, `BUILD`, `SBOM`, `SCAN`, `ASSESSMENT`,
  `COMPLIANCE`, `TEST`, `GATE`

Then, after evidence review:

- `IMPORT`, `ATTEST`, `PROMOTE`

### 4) Verify release invariants

- Digest remains unchanged across import/sign/promotion.
- Required attestations verify against the promoted subject digest.
- Quarantine and release permissions are still least-privilege.

---

## Advanced operations reference

### Artifactory layout and ownership

| Setting | Type | Primary writer |
|---|---|---|
| `FACTORY_SOURCE_REPOSITORY` | Generic | Intake only |
| `UPSTREAM_OCI_REPOSITORY` | OCI | Intake only |
| `FACTORY_BASE_QUARANTINE_REPOSITORY` | OCI | Protected importer |
| `FACTORY_APPLICATION_QUARANTINE_REPOSITORY` | OCI | Protected importer |
| `FACTORY_RELEASE_REPOSITORY` | OCI | Protected promotion broker |
| `FACTORY_CANARY_REPOSITORY` | OCI | Protected promotion broker |
| `LOCAL_RPM_CACHE_REPOSITORY` | RPM remote | Local development read-through |

Keep release repos immutable and enforce write separation by trust class.

### Jenkins Kubernetes trust classes

Each pod template setting should map to an independently governed Kubernetes
trust class with dedicated ServiceAccount and NetworkPolicy.

| Setting | Network expectation | Credential scope |
|---|---|---|
| `FACTORY_K8S_INTAKE_POD_TEMPLATE` | Connected upstream + internal | Intake-only write |
| `FACTORY_K8S_OFFLINE_POD_TEMPLATE` | Internal only | Evidence/read workloads |
| `FACTORY_K8S_BUILDKIT_POD_TEMPLATE` | Internal mirrors only | Build read-only inputs |
| `FACTORY_K8S_TEST_POD_TEMPLATE` | Internal only | Test read |
| `FACTORY_K8S_FIPS_POD_TEMPLATE` | Internal only/FIPS nodes | Compliance read |
| `FACTORY_K8S_IMPORT_POD_TEMPLATE` | Quarantine endpoints | Import write |
| `FACTORY_K8S_SIGNING_POD_TEMPLATE` | Artifactory referrers | Signing write |
| `FACTORY_K8S_PROMOTION_POD_TEMPLATE` | Source + release registries | Promotion write |

### Jenkins authorization boundary

Use separate jobs for untrusted change validation and protected release
execution. Protected credentials must not be resolvable from untrusted PR jobs.
Branch checks in an untrusted Jenkinsfile are not a security boundary.

### Bootstrap sequence (recommended)

1. Build and sign intake/factory runner images.
2. Configure Jenkins settings, pod templates, credential IDs, lock prefixes.
3. Run intake and verify lock signatures.
4. Run factory through gate-only path and inspect evidence.
5. Enable import/sign/promote with controlled approver flow.

### Day-2 cadence

| Event | Action | Evidence retained |
|---|---|---|
| Intake cycle | Review upstream lock inputs and signatures | Lock JSON + lock signature |
| Release cycle | Review gate result + compliance + test evidence | Gate result + signed predicates |
| Tool/data refresh | Rebuild/repin security data and runner images | Updated digests + version inventory |
| Pin update | Run controlled source-pin update and review | Reviewed diff + clean pipeline results |
| Key rotation | Stage and verify new signing key path | Key transition record + verification logs |

### Failure runbooks

| Symptom | Recovery focus |
|---|---|
| Build cannot resolve base | Validate digest-pinned base availability and trust-class read permissions |
| Intake publish denied | Validate intake token scope and target repository path |
| Gate denied | Inspect `work/<image>/evidence/gate-result.json` and evidence bundle |
| Missing signature/attestation after copy | Validate ORAS/referrer + Cosign attachment copy and destination permissions |
| Promotion digest mismatch | Stop promotion and investigate source/destination copy semantics |

### Rollback model

Rollback is digest-based: use a previously approved digest whose signature and
required attestations still verify. Re-run policy eligibility checks with
current data before redeployment.

### Local development operational note

Local builds are development-only and non-releasable. They are useful for
workflow validation but must not replace protected intake/build/release flows.
