.PHONY: validate test lint plan ci tekton-render tekton-check release-requests agents \
	update-pins package policy-test toolchain harness-up harness-run harness-status harness-down

PY := PYTHONPATH=. python3

validate:
	$(PY) -m factory.cli validate --catalog catalog/images

test:
	$(PY) -m unittest discover -s tests/unit -p 'test_*.py' -v

lint:
	python3 -m ruff check factory scripts tests
	python3 -m ruff format --check factory scripts tests

plan:
	$(PY) -m factory.cli plan --catalog catalog/images --all --output generated-plan.json

# Regenerate the Pipelines-as-Code PipelineRuns in .tekton/ from the catalog.
tekton-render:
	$(PY) -m factory.cli tekton-render --catalog catalog/images --output .tekton

tekton-check:
	$(PY) -m factory.cli tekton-render --catalog catalog/images --output .tekton --check

release-requests:
	$(PY) -m factory.cli release-request validate --catalog catalog/images

agents:
	$(PY) -m factory.cli agent-list

# Everything the factory-checks PipelineRun runs on a pull request.
ci: validate tekton-check release-requests lint test
	@if command -v opa >/dev/null; then $(MAKE) policy-test; else echo "opa not installed; skipping policy-test"; fi
	@mkdir -p work/factory/checks && date -u +%Y-%m-%dT%H:%M:%SZ >work/factory/checks/completed-at

update-pins:
	scripts/update_source_pins.sh

toolchain:
	toolchain/build-dist.sh dist

package:
	git archive --format=tar.gz --output=image-hardening-factory.tar.gz HEAD

policy-test:
	opa test policies/rego -v

# Local harness (docs/local-harness.md): the production Tasks on an existing
# Kubernetes 1.28+ cluster such as Rancher Desktop. Not part of make ci.
HARNESS_STATE ?= .local-factory/harness

harness-up:
	FACTORY_HARNESS_STATE="$(HARNESS_STATE)" tests/integration/local/up.sh

# Redeploys the source snapshot, Tasks, and Pipelines first, so a run always
# executes the scripts in your checkout.
harness-run:
	FACTORY_HARNESS_STATE="$(HARNESS_STATE)" tests/integration/local/deploy.sh
	$(PY) tests/integration/local/tekton.py --state "$(HARNESS_STATE)" run --image "$${IMAGE:-ubi10-minimal}"

harness-status:
	$(PY) tests/integration/local/tekton.py --state "$(HARNESS_STATE)" status

harness-down:
	FACTORY_HARNESS_STATE="$(HARNESS_STATE)" tests/integration/local/down.sh
