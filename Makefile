PYTHON ?= python3
AI_REVIEW_ROOT ?= ai-review
PYTHONPATH := $(AI_REVIEW_ROOT)/src
REVIEWER ?= claude
DIFF ?= $(AI_REVIEW_ROOT)/tests/fixtures/diffs/simple.diff
REPO ?= $(AI_REVIEW_ROOT)/tests/fixtures/repos/simple
LOCAL_OUT ?= .ai-review-local
SCOPE ?= base
RUFF_PATHS := $(AI_REVIEW_ROOT)/src $(AI_REVIEW_ROOT)/tests scripts
PYTEST_ARGS := $(AI_REVIEW_ROOT)/tests --cov=ai_review --cov-report=term-missing

.PHONY: quality test test-strict packaged-smoke lint typecheck compile supply-chain \
	release-inputs docs-check sync-workflows workflow-parity \
	update-golden review-local consensus-local validate-local

quality: docs-check lint test-strict typecheck supply-chain release-inputs workflow-parity compile

# The single gate on canonical-template -> installed-copy parity;
# `make sync-workflows` repairs the drift it reports.
workflow-parity:
	$(MAKE) --no-print-directory CHECK=1 sync-workflows

docs-check:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/check_docs.py

test:
	@if PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "import pytest" >/dev/null 2>&1; then \
		$(MAKE) --no-print-directory test-strict; \
	else \
		echo "pytest is unavailable, and there is no substitute: parts of the suite are" >&2; \
		echo "pytest-style functions that unittest cannot collect. Install the pinned" >&2; \
		echo "development dependencies and re-run:" >&2; \
		echo "" >&2; \
		echo "    $(PYTHON) -m pip install -r requirements-dev.txt" >&2; \
		exit 1; \
	fi

test-strict:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest $(PYTEST_ARGS)

# The curated packaged-runtime smoke suite that ships in the images, run by module
# name exactly as the image preflight invokes it. Not part of `make quality`: the
# checkout pytest suite above is the authoritative product test suite, and this
# asserts properties of the packaged runtime instead. SCOPE selects which image
# tag's properties to run (base or reviewer); the reviewer scope needs the pinned
# CLIs, so it is fully green only inside the reviewer image.
packaged-smoke:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review_smoke $(SCOPE)

lint:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ruff check $(RUFF_PATHS)

typecheck:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m mypy

compile:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m compileall -q $(AI_REVIEW_ROOT)/src scripts

supply-chain:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/check_supply_chain_pins.py

release-inputs:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/check_release_inputs.py

# Pass CHECK=1 to verify without writing.
sync-workflows:
	PYTHONPATH=$(PYTHONPATH):scripts $(PYTHON) scripts/sync_workflows.py $(if $(CHECK),--check,)

update-golden:
	PYTHONPATH=$(PYTHONPATH):$(AI_REVIEW_ROOT)/tests $(PYTHON) $(AI_REVIEW_ROOT)/tests/contract/update_golden_consensus.py

review-local:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.input_bundle local --config $(AI_REVIEW_ROOT)/config/review.yaml --diff $(DIFF) --repo $(REPO) --out $(LOCAL_OUT)/inputs
	AI_REVIEW_INPUT_DIR=$(LOCAL_OUT)/inputs AI_REVIEW_OUTPUT_DIR=$(LOCAL_OUT)/out AI_REVIEW_CONFIG=$(AI_REVIEW_ROOT)/config/review.yaml AI_REVIEW_LOCAL_MOCK=1 AI_REVIEW_ALLOW_LOCAL_MOCK=true PYTHONPATH=$(PYTHONPATH) ./$(AI_REVIEW_ROOT)/adapters/run_reviewer.sh $(REVIEWER) review

consensus-local: review-local
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.consensus --config $(AI_REVIEW_ROOT)/config/review.yaml --inputs $(LOCAL_OUT)/inputs --findings-dir $(LOCAL_OUT)/out/findings --out $(LOCAL_OUT)/out/consensus/consensus.json
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.schema validate --schema consensus.schema.json --input $(LOCAL_OUT)/out/consensus/consensus.json

validate-local: review-local
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.schema validate --schema finding_batch.schema.json --input $(LOCAL_OUT)/out/findings/$(REVIEWER).json
