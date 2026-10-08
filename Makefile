.PHONY: validate test lint plan ci tekton-render tekton-check release-requests agents \
	local-build local-test local-assessment update-pins package policy-test

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

local-build:
	@test -n "$(IMAGE)" || { echo "usage: make local-build IMAGE=ubi9-minimal [LOCAL_RPM_REPO_DIR=/path/to/snapshot]" >&2; exit 2; }
	scripts/local_build.sh "$(IMAGE)"

local-test: local-build
	scripts/run_tests.sh "catalog/images/$(IMAGE).yaml" "work/$(IMAGE)"

local-assessment: local-build
	scripts/scan_image.sh "catalog/images/$(IMAGE).yaml" "work/$(IMAGE)"

package:
	git archive --format=tar.gz --output=image-hardening-factory.tar.gz HEAD

policy-test:
	opa test policies/rego -v
