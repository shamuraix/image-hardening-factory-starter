.PHONY: validate test lint pipeline local-build local-test local-fcs package

validate:
	PYTHONPATH=. python3 -m factory.cli validate --catalog catalog/images

test:
	PYTHONPATH=. python3 -m unittest discover -s tests/unit -p 'test_*.py' -v

lint:
	ruff check factory scripts tests
	ruff format --check factory scripts tests

pipeline:
	PYTHONPATH=. python3 -m factory.cli pipeline --catalog catalog/images --all --output generated-child.yml

local-build:
	@test -n "$(IMAGE)" || { echo "usage: make local-build IMAGE=ubi9-minimal LOCAL_RPM_REPO_DIR=/path/to/snapshot" >&2; exit 2; }
	scripts/local_build.sh "$(IMAGE)"

local-test: local-build
	scripts/run_tests.sh "catalog/images/$(IMAGE).yaml" "work/$(IMAGE)"

local-fcs: local-build
	scripts/fcs_scan_image.sh "catalog/images/$(IMAGE).yaml" "work/$(IMAGE)"

package:
	git archive --format=tar.gz --output=image-hardening-factory.tar.gz HEAD
