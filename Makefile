PYTHON ?= python3
AI_REVIEW_ROOT ?= ai-review
PYTHONPATH := $(AI_REVIEW_ROOT)/src
REVIEWER ?= claude
DIFF ?= $(AI_REVIEW_ROOT)/tests/fixtures/diffs/simple.diff
REPO ?= $(AI_REVIEW_ROOT)/tests/fixtures/repos/simple
LOCAL_OUT ?= .ai-review-local
RUFF_PATHS := $(AI_REVIEW_ROOT)/src $(AI_REVIEW_ROOT)/tests scripts
PYTEST_ARGS := $(AI_REVIEW_ROOT)/tests --cov=ai_review --cov-report=term-missing

.PHONY: quality test test-strict test-fallback lint typecheck compile supply-chain \
	release-inputs docs-check \
	update-golden review-local consensus-local validate-local

quality: docs-check lint test-strict typecheck supply-chain release-inputs compile

docs-check:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/check_docs.py

test:
	@if PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "import pytest" >/dev/null 2>&1; then \
		$(MAKE) --no-print-directory test-strict; \
	else \
		echo "pytest is unavailable; running the documented local unittest fallback"; \
		$(MAKE) --no-print-directory test-fallback; \
	fi

test-strict:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest $(PYTEST_ARGS)

test-fallback:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s $(AI_REVIEW_ROOT)/tests -p 'test_*.py'

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

update-golden:
	PYTHONPATH=$(PYTHONPATH):$(AI_REVIEW_ROOT)/tests $(PYTHON) $(AI_REVIEW_ROOT)/tests/contract/update_golden_consensus.py

review-local:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.input_bundle local --config $(AI_REVIEW_ROOT)/config/review.yaml --diff $(DIFF) --repo $(REPO) --out $(LOCAL_OUT)/inputs
	AI_REVIEW_INPUT_DIR=$(LOCAL_OUT)/inputs AI_REVIEW_OUTPUT_DIR=$(LOCAL_OUT)/out AI_REVIEW_CONFIG=$(AI_REVIEW_ROOT)/config/review.yaml AI_REVIEW_LOCAL_MOCK=1 PYTHONPATH=$(PYTHONPATH) ./$(AI_REVIEW_ROOT)/adapters/run_reviewer.sh $(REVIEWER) review

consensus-local: review-local
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.consensus --config $(AI_REVIEW_ROOT)/config/review.yaml --inputs $(LOCAL_OUT)/inputs --findings-dir $(LOCAL_OUT)/out/findings --out $(LOCAL_OUT)/out/consensus/consensus.json
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.schema validate --schema consensus.schema.json --input $(LOCAL_OUT)/out/consensus/consensus.json

validate-local: review-local
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.schema validate --schema finding_batch.schema.json --input $(LOCAL_OUT)/out/findings/$(REVIEWER).json
