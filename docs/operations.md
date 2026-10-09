# Operations guide

[Project overview](../README.md) · [Configuration](configuration.md) · [Architecture](architecture.md) · [Agents](agents.md) · [Evidence model](evidence-model.md)

Terms: **PaC** is Pipelines-as-Code, which starts the Tekton pipelines. The
**gate** is the OPA (Open Policy Agent) release policy. A **seal** is a list of
SHA-256 hashes a stage writes for its outputs so later stages can detect changes
(see [architecture.md](architecture.md#sealing-stage-outputs)).

## Bootstrap sequence

1. On a connected host run `make toolchain`, then build and sign the runner,
   intake-runner, and agent images from `toolchain/` and record their digests
   in `deploy/base/repository.yaml` (see
   [configuration.md](configuration.md#runner-images)).
2. Install Tekton Pipelines, Pipelines-as-Code, and Tekton Chains; apply
   `deploy/chains/chains-config.yaml`.
3. Create Artifactory repositories for source, upstream OCI, quarantine,
   release, and canary content, with the write separation in the table below.
4. Create the Secrets (`deploy/secrets.example.yaml`), edit settings and network
   CIDRs, then run `kubectl apply -k deploy/base`. Allow the `approved-upstreams`
   NetworkPolicy to reach the Grype, Trivy, OSV, and ClamAV database mirrors and
   the CISA KEV feed.
5. Build the first security-data bundle:
   `scripts/tekton/trigger_incoming.sh security-data-on-schedule`. Confirm that
   `security-data/current.json` exists in `FACTORY_SOURCE_REPOSITORY`. Every
   image build's prepare stage fails until it does.
6. Run intake: `scripts/tekton/trigger_incoming.sh intake-on-schedule`. Confirm
   signed locks under `locks/<image>/<revision>/`. Both trigger commands need
   the `FACTORY_PAC_*` environment variables and the incoming secret.
7. Open a trivial PR (for example, a comment change in
   `catalog/images/jira-lts.yaml`) to run the PR pipeline end to end. Inspect the
   evidence in the TaskRun logs and workspace.
8. Merge it; review the quarantine import and the release request PR.
9. Merge a **commercial** release request first. Enable gov1/gov2 only after the
   approver flow and environment keys are verified.

## Releasing an image

1. A merge to `main` that changes an image's inputs (or a base release) builds,
   gates, imports to quarantine, and opens
   `release(<env>): <image> <version> @ <digest>`.
2. Read the PR: the generated request (digest, evidence digests, gate warnings)
   and the `release-readiness` briefing.
3. Approve per CODEOWNERS. **Merging is the approval of record.** For gov1/gov2
   the person who merges must match `FACTORY_GOV_APPROVER_PATTERN`.
4. `release-on-push` signs, attests, promotes by digest, and re-verifies. For a
   base image in the pointer environment it also publishes
   `releases/<base>/current.json` and starts the dependents' `on-base-release`
   runs.

To release the same candidate to another environment, copy the request file to
`releases/<other-env>/<image>.yaml`, set `metadata.environment`, and open a PR.
Only one release request may change per merge (enforced).

## Artifactory layout and ownership

| Setting | Type | Only writer |
|---|---|---|
| `FACTORY_SOURCE_REPOSITORY` | Generic | intake (files, locks); security-data (bundle and `security-data/current.json`); promote (release pointers, separate credential) |
| `UPSTREAM_OCI_REPOSITORY` | OCI | intake |
| `FACTORY_BASE_QUARANTINE_REPOSITORY` / `FACTORY_APPLICATION_QUARANTINE_REPOSITORY` | OCI | quarantine task |
| `FACTORY_RELEASE_REPOSITORY` / `FACTORY_CANARY_REPOSITORY` | OCI | promote task |

Keep release repositories immutable.

## Day-2 cadence

All times are UTC and come from the CronJobs in `deploy/base/schedules.yaml`.

| When | CronJob → what runs | Human action |
|---|---|---|
| Every PR | factory-checks, image PR builds, triage on failure, pipeline-reviewer on risky paths | review comments and evidence |
| Daily 00:37 | `daily-security-data` → `security-data-on-schedule` (rebuild, sign, and publish the security-data bundle) | check it succeeded; a failure makes builds deny once the data is older than 72 hours |
| Nightly 01:47 | `nightly-base-rescan` → `ubi9-minimal`, `ubi10-minimal` rescans | review remediation PRs |
| Nightly 02:17 | `nightly-rescan` → `bitbucket-lts`, `confluence-lts`, `jira-lts` rescans | review remediation PRs |
| Weekdays 05:07 | `daily-upstream-sync` → intake and upstream-sync agent | review upstream PRs; run intake after merging new pins |
| Mondays 06:23 | `weekly-exception-review` → exception-steward agent | approve or close removal PRs; act on review-needed items |
| Each release request | readiness briefing | approve and merge |
| Tool updates | Renovate PRs to `tools/versions.lock.yaml` | review, rebuild and re-sign runner images, bump digests |
| Key rotation | — | stage the new environment key Secret, verify, swap |

## Failure runbooks

| Symptom | Start with |
|---|---|
| Triage comment says `policy-deny` | the deny list in `gate-result.json`; wait for or review the remediation PR |
| Prepare fails in `fetch_security_bundle.sh` | does `security-data/current.json` exist? Did the last `security-data-on-schedule` run succeed? A digest, signature, or manifest mismatch means the bundle was changed after signing: do not retry blindly |
| Scan stage `failed`, then assessment fails with no assessment status | Grype validation stopped the scan; read `logs/scan.log` and the table below |
| Gate deny `scanner database is stale, missing or from the future` | age of the bundle and of the Grype database (`evidence/database-status.json`); check the security-data schedule |
| Many new HIGH findings denied by `newHigh` | no signed baseline is in use, so every finding counts as new (see [evidence-model.md](evidence-model.md#baselines-and-new-findings)) |
| `seal <stage> manifest was modified` / `files sealed by <stage> were modified` | treat as tampering: keep the volume and TaskRuns, check who could write to the workspace, do not retry blindly |
| `repository checkout was modified by an earlier task` | a task wrote outside `work/`; find it in the TaskRun list and fix the script |
| Build cannot resolve base | does the release pointer exist (`releases/<base>/current.json`)? Is the digest-pinned base in the internal registry? Does the read token have the right scope? |
| Overlay `git apply --check` fails after a pin move | upstream changed; run or re-run the upstream-sync agent, or rebase by hand (skill `ironbank-overlays`) |
| Release request rejected by resolve | more than one request in the merge, or path and contents do not match |
| `evidence manifest is not attached to <digest>` | wrong request contents, or quarantine was pushed again; regenerate the request from the build |
| Admission policy denies a pod | a PR run referenced a protected Secret; a trigger or task change is wrong. Fix it in `factory/tekton.py` or the Task |
| Agent outcome `change-rejected` | the agent tried to write outside its contract; read the note and the agent log; tighten the persona if needed |
| Agent outcome `agent-failed` | gateway reachability or token, budget or turn cap, schema mismatch; see `agent.stderr.log` in the agent workspace |
| Promotion digest mismatch | stop; investigate source and destination copy behavior |

### Grype validation errors

These messages come from `factory/grype.py` and stop the scan stage.

| Message | Meaning and fix |
|---|---|
| `Grype database is invalid` / `Grype database timestamp must have a timezone` | the bundle's Grype database is broken; rebuild the bundle |
| `Grype report identity is missing`, `Grype vulnerability is incomplete`, and similar | the Grype report is malformed; check the Grype version in the runner image |
| `SBOM was generated for another image` / `SBOM changed after generation` | the SBOM does not match the candidate; treat as tampering or a stage ordering bug |
| `KEV feed is stale or from the future` | the KEV catalog's `dateReleased` is outside `policy.maximumDatabaseAgeHours`. If the bundle is fresh, CISA has not updated the catalog for more than 72 hours; a human must decide how to handle this (see [security-data/README.md](../security-data/README.md#freshness)). Never add scanner ignores |
| cosign error, or `Baseline approval or image identity is invalid` | a signed baseline exists but does not verify or is not approved for this image |

## Rollback

Rollback is by digest: open a release request for a previously approved digest
whose signatures and attestations still verify (its old request file is in git
history). Re-check policy with current data before redeploying.

## Local development

There is no host-side build. To iterate on an overlay, run the kind harness
(`make harness-up`, `make harness-run`), which builds the image with the same
Tekton Task the pipeline uses. Harness builds carry a `localDevelopment` lock
and can never be imported, signed, or promoted. See
[local-kubernetes-testing.md](local-kubernetes-testing.md).
