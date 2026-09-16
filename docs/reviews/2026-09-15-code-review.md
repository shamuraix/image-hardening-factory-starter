# Repository review — 2026-09-15

Reviewed baseline: `cf4d1e1`. The added kind harness is separate from the
production pipeline. Findings below describe the original baseline; see
[harness-remediation-plan.md](harness-remediation-plan.md) for implemented fixes
and remaining acceptance work.

## Confirmed findings

### R10 — High: Empty authentication files prevent registry login

Locations: `scripts/import_image.sh:34`, `scripts/publish_intake.sh:49`,
and remote base acquisition in `scripts/build_image.sh`.

These paths create a zero-byte file with mktemp and immediately pass that
existing file as Skopeo's authfile. The live Debian runner's Skopeo rejects it
as invalid JSON (`unexpected end of JSON input`) before login. This stopped
quarantine import in the kind integration run. The signing path succeeds
because it creates a directory and uses a not-yet-existing config.json path.

Initialize authfiles with a valid empty Docker authentication object, or use a
private directory with a nonexistent filename, as the signing script does.
Verify with the exact production Skopeo build as well as the harness version.

### R11 — High: Promotion uses an unsupported ORAS 1.3.0 flag

Location: `scripts/promote_image.sh:36`.

The real pinned ORAS 1.3.0 binary rejects `oras cp --registry-config` with
`unknown flag: --registry-config`. In the kind test, signing and verification
of every required source predicate succeeded, then promotion failed at this
command. `oras cp` exposes separate `--from-registry-config` and
`--to-registry-config` options. Supply both explicitly and retain the existing
Cosign attachment copy and destination verification.

### R1 — High: RPM verification errors can be reported as success

Location: `scripts/assert_rpm_integrity.sh:16`.

The container executes `rpm -Va --nomtime || true`, discards the original
status, and accepts empty stdout. A database-open error, missing executable,
or other operational failure that emits only stderr therefore passes the
integrity check. This check contributes to the blocking product-test result.
A local test double returning an RPM database error and status 2 reproduced
wrapper status 0 with a zero-byte evidence file.

Preserve status and stderr, establish that the package database is readable and
contains the expected inventory, and distinguish allowed verification changes
from scanner execution errors. Add a regression for an unreadable database.

### R2 — Medium: Gate denial can leave the overall Jenkins build successful

Locations: `Jenkinsfile:196`, gate invocation around `Jenkinsfile:457`.

The authoritative gate uses the same `nonBlocking` behavior as optional tools:
`catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE')`. If an FCS denial
reaches the gate and IMPORT is disabled, the job can finish SUCCESS despite
failing the gate. This is a valid combination of exposed stage parameters.
Protected import independently denies publication, so this is a CI/status
failure rather than a demonstrated release bypass.

Continue to remediation while retaining an overall FAILURE/UNSTABLE result for
the authoritative gate. Keep optional scanner status handling separate.
[Jenkins documents the independent build and stage result behavior](https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/#catcherror-catch-error-and-set-build-result-to-failure).

### R3 — Medium: User cancellation is caught as an optional-stage error

Location: `Jenkinsfile:196`.

`catchError` defaults to catching interruptions, including manual aborts. A
cancel during an optional stage may be caught and execution may proceed into
later stages, including publication stages if already enabled. Set
`catchInterruptions: false` and verify cancellation while an optional shell
step is running. The harness uses that setting explicitly.
[Jenkins interruption semantics](https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/#catcherror-catch-error-and-set-build-result-to-failure).

### R4 — Medium: Snapshot pipeline hides the producer's failure status

Locations: `Jenkinsfile.intake:158` and `Jenkinsfile.intake:179`.

The snapshot script is piped into `tail -n1` under a shell that has not enabled
pipefail. A failed producer still yields a successful pipeline when tail exits
zero, and an empty or unrelated last output line is recorded as a snapshot ID.
This was reproduced with `/bin/sh -e` and a producer exiting 42.

Capture and check the snapshot command separately, or explicitly invoke Bash
with pipefail; validate the final snapshot ID before writing the environment
artifact. This does not demonstrate bypass of subsequent signature checks.

### R5 — Medium: Blocking failures skip evidence archival

Location: `Jenkinsfile:200` through `Jenkinsfile:212`.

For blocking stages, a thrown shell failure skips both stash and archive.
Compliance and product tests write diagnostic reports before returning failure;
those reports are not archived on the failure path and may disappear with the
pod. Preserve available diagnostics in finally, without masking the original
failure or handing incomplete output to dependent stages.

### R6 — Medium: Default scanner cache variables are not exported

Location: `scripts/scan_image.sh:9`.

The three cache variables are assigned as shell variables. When initially
unset, Grype/Trivy/OSV child processes do not receive them. The runner bundles
its databases under `/opt/security-data`, while tools can instead search their
own default locations. In an offline pod this can prevent informational scans
from running. The gate then substitutes unavailable legacy evidence.

A mock Grype invoked by the real wrapper confirmed that the default variable
was absent from its environment. Export these values explicitly and run an
offline scanner smoke test with only the bundled databases available.

### R7 — Medium: Several stages do not receive files their commands consume

Locations: `Jenkinsfile:366`, `Jenkinsfile:397`, `Jenkinsfile:600`.

Each stage uses a fresh checkout and only its declared stashes:

- SCAN receives build and SBOM, but OSV searches `work/<image>/context` from
  PREPARE. That directory is absent. The failed find is treated as no matching
  project and an empty OSV result is written.
- COPA exposes `FACTORY_COPA_FINDINGS` but never unstashes scan findings. A hook
  that requires that advertised file fails even when SCAN ran successfully.
- HUMMINGBIRD reads gate, FCS, and provenance files, but receives only build and
  SBOM. Its summary consequently reports false assessment/gate values and an
  empty provenance checksum, including after successful earlier stages.

A fresh-workspace reproduction confirmed the Copa missing-file failure and
Hummingbird's completed summary containing false/empty verification fields.
Declare all required/optional artifact inputs explicitly and represent missing
optional evidence as unavailable, rather than a completed false assessment.
Add tests that materialize exactly the stage's declared stashes.

### R8 — Medium: Alternate catalog directories are not respected consistently

Locations: `scripts/publish_intake.sh:5`, `scripts/validate_context.py:35`,
`scripts/validate_image.sh:17`, and base lookup in `scripts/build_image.sh`.

Jenkins accepts `FACTORY_CATALOG_DIR`, but these helpers hardcode
`catalog/images`. Intake can resolve resources from one catalog then publish
using another catalog's revision or family. Base validation and build-argument
selection can likewise read the wrong definition. Some paths also assume the
filename is identical to metadata.name, which catalog validation does not
require. Pass resolved catalog paths throughout the pipeline and centralize
base lookup.

### R9 — Medium: FCS report validation accepts unrelated JSON objects

Location: `scripts/fcs_scan_image.sh:80`.

`reportValid` checks only that the assessment is a nonempty JSON object. With
zero CLI statuses and a syntactically valid SBOM, an object such as
`{"error":"assessment unavailable"}` becomes `reportValid:true` and
`assessmentPassed:true`. Executing the actual validation/status block with
that fixture made the actual OPA policy return allow=true.

This proves insufficient validation; it does not establish that the real FCS
4.0.0 CLI emits that fixture with status zero. Obtain success, denial, and error
fixtures from the pinned CLI and validate its actual assessment shape,
completion and identity fields. The current digest field in status is copied
from the requested candidate, not extracted from the assessment.

## Other maintenance and integration concerns

- `toolchain/Containerfile.factory-runner` installs the Python package into
  Python 3.12, while commands/shebangs invoke `python3`. Verify those resolve to
  the same installation in the actual runner; an older default Python can fail
  imports even though the image build succeeds.
- Local application builds reuse a base archive solely because it exists
  (`scripts/local_build.sh:68`). Changing a base revision, overlay, or snapshot
  does not invalidate that cached base. This is development-only, but can make
  local validation misleading.
- Several tests assert source-text substrings rather than command behavior or
  stage artifact contracts. The missing inputs above are not caught by those
  assertions. The unit suite also inherits FACTORY_BUILD_ID: setting it to a
  CI value reproduced an assertion that assumed the tag would be `local`.
- FCS schema compatibility, report naming, policy-denial exit behavior, and
  strict digest behavior require the licensed pinned CLI and a test tenant.
  The official action supports the command flags used here, but is not a
  substitute for executing the pinned binary against real fixtures.
- Legacy catalog thresholds and unsigned operator-managed current-base pointers
  are explicitly documented implementation choices. They were not treated as
  newly discovered policy enforcement defects.
- The default BuildKit pod is intentionally outside Restricted/Baseline policy
  and disables the process sandbox. This is documented. Test admission and
  egress in the intended deployment; a functioning kind cluster does not prove
  production tenant isolation or FIPS compliance.

## Local verification

- All 83 existing Python unit tests passed in the original local environment.
- All 7 existing OPA tests passed.
- All 5 catalog definitions validated.
- Bash syntax checks passed for all 37 pre-existing shell scripts.
- ShellCheck reported no warnings or errors; 21 informational diagnostics
  concern trap reachability and an intentional `&& ... || ...` expression.
- Ruff 0.16.7 was run from a temporary tool environment using the project config: 8 pre-existing lint findings remain in `factory/buildkit.py`, `tests/unit/test_buildkit.py`, and `tests/unit/test_buildkit_runner.py`. All 25 Python files pass formatting; the new harness client passes lint.
- Reproductions covered RPM fail-open behavior, missing exported scanner
  defaults, missing Copa inputs, incorrect Hummingbird summary inputs, and
  the FCS validation-block/OPA acceptance of a non-assessment object.

## Kind integration harness

See [harness instructions](../../tests/integration/kind/README.md).
The harness uses an isolated `factory-review` rootless-Podman kind cluster,
Jenkins, two fresh Kubernetes agent pods, a TLS registry, and a separate Debian
runner with the pinned BuildKit, RootlessKit, Cosign and ORAS binaries.
It does not replace the production runner or production trust configuration.

Live results and remaining prerequisites are recorded in the harness README
and local `.local-factory/kind-review/` logs.

### Observed integration results — Jenkins build 6

Jenkins job: <http://127.0.0.1:18080/job/factory-harness/6/>.
Overall result: **FAILURE**, intentionally preserving the production-script
failures instead of treating independent diagnostic probes as fixes.

| Check | Result |
| --- | --- |
| Kubernetes agent provisioning with UID 10001 | Passed after harness workspace configuration |
| Existing unit / OPA tests inside the pod | 83 / 7 passed |
| Cross-pod stash / unstash checksum | Passed |
| TLS registry push / pull and digest preservation | Passed |
| Rejection of denied gates and local-development imports | Passed |
| Unmodified production importer | Failed: empty authentication JSON (R10) |
| Unmodified signing script; required source attestations | Passed with synthetic evidence |
| Unmodified promotion script | Failed: unsupported ORAS flag (R11) |
| Independent corrected ORAS + Cosign copy, destination verification and digest | Passed |
| Rootless BuildKit | Blocked: nested newuidmap operation denied |
| Real failing-RUN cleanup and rootless Podman execution | Not reached because BuildKit could not start |

The mapping failure persisted with a test-only subordinate range that fits
inside the parent rootless-kind map. The helpers are setuid and
NoNewPrivs=0, but the runtime still rejects the mapping. No host security
settings or subordinate-ID allocations were changed. Completing these tests
needs a host/runtime supporting the nested mapping, or another kind provider
such as a rootful container runtime. This is an environment limitation observed
before BuildKit execution, not proof that the repository's BuildKit logic is
incorrect.

The harness uses Skopeo 1.9.3 and Podman 4.3.1 from Debian, Python 3.11.2,
BuildKit 0.33.0, RootlessKit 3.1.0, Cosign 2.6.0, and ORAS 1.3.0. Repeat the
authfile test with the production Skopeo/RPM runner before assessing deployment
impact. Registry access is unauthenticated within the local test service, so
successful transport does not establish production permission isolation.

Collected logs, predicates and plugin versions are stored under
`.local-factory/kind-review/results/6/`; exact tool output is in
`.local-factory/kind-review/runner-versions.txt`. Jenkins and registry remain
running for inspection; the temporary diagnostic pod was removed.
