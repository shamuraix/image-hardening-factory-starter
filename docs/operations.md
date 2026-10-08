# Operations guide

[Project overview](../README.md) · [Configuration](configuration.md) · [Architecture](architecture.md) · [Agents](agents.md)

## Bootstrap sequence

1. Build and sign the runner, intake-runner, and agent images; record digests in
   `deploy/base/repository.yaml`.
2. Install Tekton Pipelines, Pipelines-as-Code, and Chains; apply
   `deploy/chains/chains-config.yaml`.
3. Create Artifactory repositories for source, upstream OCI, quarantine, release,
   and canary flows with the write separation in the table below.
4. Create the Secrets (`deploy/secrets.example.yaml`), edit settings and network
   CIDRs, and `kubectl apply -k deploy/base`.
5. Trigger intake: `scripts/tekton/trigger_incoming.sh intake-on-schedule` (needs
   `FACTORY_PAC_*` env and the incoming secret) and confirm signed locks under
   `locks/<image>/<revision>/`.
6. Open a trivial PR touching `catalog/images/jira-lts.yaml` comments to exercise
   the PR pipeline end to end; inspect evidence in the TaskRun logs and workspace.
7. Merge it; review the quarantine import and the release request PR.
8. Merge a **commercial** release request first. Enable gov1/gov2 only after the
   approver flow and environment keys are verified.

## Releasing an image

1. A merge to `main` that changes an image's inputs (or a base release) builds,
   gates, imports to quarantine, and opens `release(<env>): <image> <version> @ <digest>`.
2. Read the PR: the deterministic request (digest, evidence digests, gate
   warnings) and the `release-readiness` briefing.
3. Approve per CODEOWNERS. **Merging is the approval of record**; for gov1/gov2 the
   merger must match `FACTORY_GOV_APPROVER_PATTERN`.
4. `release-on-push` signs, attests, promotes by digest, re-verifies, and — for a
   base image in the pointer environment — publishes `releases/<base>/current.json`
   and triggers dependents' `on-base-release` runs.

To release the same candidate to another environment, copy the request file to
`releases/<other-env>/<image>.yaml`, set `metadata.environment`, and open a PR.
One release request per merge (enforced).

## Artifactory layout and ownership

| Setting | Type | Only writer |
|---|---|---|
| `FACTORY_SOURCE_REPOSITORY` | Generic | intake (files, locks); promote (release pointers, separate credential) |
| `UPSTREAM_OCI_REPOSITORY` | OCI | intake |
| `FACTORY_BASE_QUARANTINE_REPOSITORY` / `FACTORY_APPLICATION_QUARANTINE_REPOSITORY` | OCI | quarantine task |
| `FACTORY_RELEASE_REPOSITORY` / `FACTORY_CANARY_REPOSITORY` | OCI | promote task |

Keep release repositories immutable.

## Day-2 cadence

| When | What runs | Human action |
|---|---|---|
| Every PR | factory-checks, image PR builds, triage on failure, pipeline-reviewer on risky paths | review comments and evidence |
| Nightly 01:47/02:17 UTC | base and app rescans | review remediation PRs |
| Weekdays 05:07 UTC | intake, upstream-sync | review upstream PRs; run intake after merging new pins |
| Mondays 06:23 UTC | exception-steward | approve or close removal PRs; act on review-needed items |
| Each release request | readiness briefing | approve/merge |
| Tool/data refresh | Renovate PRs, security-data bundle rebuild | review, rebuild and re-sign runner images, bump digests |
| Key rotation | — | stage new env key Secret, verify, swap |

## Failure runbooks

| Symptom | Start with |
|---|---|
| Triage comment says `policy-deny` | `gate-result.json` deny list; wait for or review the remediation PR |
| `seal <stage> manifest was modified` / `files sealed by <stage> were modified` | treat as tampering: preserve the PVC and TaskRuns, check who could write to the workspace, do not retry blindly |
| `repository checkout was modified by an earlier task` | a task wrote outside `work/`; find it in the TaskRun list and fix the script |
| Build cannot resolve base | release pointer exists? (`releases/<base>/current.json`), digest-pinned base in the internal registry, read token scope |
| Overlay `git apply --check` fails after a pin move | upstream changed; run or re-run the upstream-sync agent, or rebase by hand (skill `ironbank-overlays`) |
| Release request rejected by resolve | more than one request in the merge, or path/contents mismatch |
| `evidence manifest is not attached to <digest>` | wrong request contents or quarantine was re-pushed; regenerate the request from the build |
| Admission policy denies a pod | a PR run referenced a protected Secret — a trigger or task change is wrong; fix it in `factory/tekton.py` or the Task |
| Agent outcome `change-rejected` | the agent tried to write outside its contract; read the note and the agent log; tighten the persona if needed |
| Agent outcome `agent-failed` | gateway reachability/token, budget or turn cap, schema mismatch; see `agent.stderr.log` in the agent workspace |
| Promotion digest mismatch | stop; investigate source/destination copy semantics |

## Rollback

Rollback is digest-based: open a release request for a previously approved
digest whose signatures and attestations still verify (its old request file is in
git history). Re-run policy eligibility with current data before redeploying.

## Local development

Local builds are development-only and non-releasable. Use them to iterate on
overlays and tests; CI and the release pipeline remain the only path to release.
