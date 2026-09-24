# Image Hardening Factory

Image Hardening Factory rebuilds pinned UBI and Atlassian image sources with a
repeatable pipeline and produces:

- OCI image archives
- SBOMs
- vulnerability/compliance/test evidence
- signed release artifacts

It is a build-and-evidence system (not a deployment platform).

## New here? Start in 10 minutes

### 1) Install required dependencies

Minimum local prerequisites:

- Python 3.11+
- `git`, `curl`
- `jq`, `yq`
- Podman
- Skopeo
- Lima (`limactl`) with `template://buildkit`
- BuildKit client (`buildctl`)
- `umoci` (required by scan/compliance scripts)

Optional but commonly needed:

- `ruff` (installed by `pip install -e '.[dev]'`)
- `opa` (for `make policy-test`)

> Use versions from `tools/versions.lock.yaml` where possible.

### 2) Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
```

Offline install example:

```bash
pip install --no-index --find-links /path/to/wheels -e '.[dev]'
```

### 3) Run basic repo checks

```bash
make validate
make test
make lint
make plan
```

What these do:

- `validate` — validates catalog definitions
- `test` — runs Python unit tests
- `lint` — runs Ruff checks and formatting validation
- `plan` — generates `generated-jenkins-plan.json` (no build/publish)

### 4) Run a local build (development-only)

```bash
limactl start --name factory-buildkit template://buildkit
export FACTORY_BUILDKIT_LIMA_INSTANCE=factory-buildkit
make local-build IMAGE=ubi9-minimal LOCAL_USE_UPSTREAM_UBI_REPOS=true
```

Then run tests and scan/assessment:

```bash
make local-test IMAGE=ubi9-minimal
make local-assessment IMAGE=ubi9-minimal
```

All local outputs are marked development-only and are not releasable.

---

## Repository quick map

| Path | Purpose |
|---|---|
| `factory/` | Python package for catalog loading, validation, planning, and evidence shaping |
| `scripts/` | Build, scan, test, intake, gate, signing, and promotion scripts |
| `catalog/images/` | Image catalog entries (what to build and policy controls) |
| `policies/rego/` | OPA release policy and policy tests |
| `tests/` | Unit and integration test coverage |
| `config/` | Bundled config defaults (including RPM repo definitions) |
| `docs/` | Architecture, operations, local development, and configuration references |

---

## Core contributor workflow

1. Make changes
2. Run checks:

   ```bash
   make validate
   make test
   make lint
   make policy-test
   ```

3. Open PR with a clear summary and evidence

See also: [CONTRIBUTING.md](CONTRIBUTING.md)

---

## Documentation guide (where to read next)

- [Local development](docs/local-development.md) — workstation build/test/assessment flow
- [Architecture](docs/architecture.md) — trust boundaries and stage model
- [Configuration](docs/configuration.md) — Jenkins settings, credentials, toggles
- [Operations](docs/operations.md) — operational guidance and release flow
- [Evidence model](docs/evidence-model.md) — expected evidence artifacts
- [Implementation status](docs/implementation-status.md) — what is complete vs. pending

---

## Notes and expectations

- This repo is an integration reference implementation and evolves actively.
- Production use requires environment-specific Jenkins, credentials, signing keys,
  repositories, and test environments.
- Do not commit generated outputs (`work/`, OCI archives, downloaded proprietary
  binaries, or credentials).
