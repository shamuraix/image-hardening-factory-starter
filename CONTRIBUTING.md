# Contributing

Use Python 3.11+ and run from the repository root. Install `.[dev]` from your
approved package mirror. Run `make validate test lint policy-test` before a PR.
`policy-test` requires OPA; Python tests alone do not evaluate Rego.

Keep source revisions immutable. A pin update must include matching product
versions, build arguments, manifest checksums and an overlay applicability test
against the new upstream commit. `make update-pins` only changes revisions; it
does not prove those other inputs still agree. Never automatically merge its diff.

Add behavioral regression tests for failures that can affect release decisions,
artifact identity or trust boundaries. Use local mock tools to test command
contracts without registry credentials, and retain environment integration tests
for container/runtime behavior that mocks cannot establish.

The JSON Schema lives in `factory/schemas/` and ships inside the wheel. Do not
introduce a second schema under the catalog. Stage implementations remain shell
scripts; the Python package handles reusable data validation and planning.

Tool versions in `tools/versions.lock.yaml` are a reviewed inventory, not an
installer lock: Containerfiles still install some RPM tools from their configured
repositories. Stage verified binaries and wheels under ignored `dist/`, build
against an immutable RPM source, verify actual versions, then pin the resulting
runner image digest. Review tool upgrades individually instead of replacing all
pins with moving latest versions.

Never commit credentials, downloaded proprietary binaries, generated OCI images
or licensed test fixtures. Keep policy/approval and publication changes separate
from AI-generated remediation proposals. The broker creates a branch, not an
approved release or an automatically merged pull request.
