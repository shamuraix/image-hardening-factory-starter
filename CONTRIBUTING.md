# Contributing

## Quick contributor workflow

1. Use Python 3.11+ from repository root.
2. Install dependencies:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install -e '.[dev]'
   ```

3. Run checks before opening a PR:

   ```bash
   make validate
   make test
   make lint
   make policy-test
   ```

`policy-test` requires OPA; Python tests do not validate Rego policy behavior.

---

## Advanced contribution rules

### Source pin changes

`make update-pins` updates source revisions only. A valid pin-change PR must also
confirm matching product versions, build arguments, manifest checksums, and
overlay applicability against the new upstream revision.

`make update-pins` runs `vendir sync` by default to refresh `vendor/repo1/`.
Set `FACTORY_VENDIR_SYNC=false` only when you intentionally need metadata-only
pin updates.

Never auto-merge source pin diffs.

### Required testing depth

Add regression tests for changes that affect:

- release decisions
- digest/evidence identity
- trust boundaries

Use local mock tools for command contracts and integration tests for runtime
behavior that mocks cannot prove.

### Repository conventions

- Catalog schema remains under `factory/schemas/` (packaged in wheel).
- Keep shell stage scripts as stage executors; use Python package code for
  reusable validation/planning/data shaping.
- `tools/versions.lock.yaml` is a reviewed inventory, not an installer lock.

### Security and artifact handling

Never commit:

- credentials/secrets
- proprietary downloaded binaries
- generated OCI images
- licensed test fixtures

Keep policy/approval/publication changes separate from AI remediation proposals.
AI-generated changes are never approval by themselves.
