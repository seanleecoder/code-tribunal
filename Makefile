PYTHON ?= python3
AI_REVIEW_ROOT ?= ai-review
PYTHONPATH := $(AI_REVIEW_ROOT)/src
REVIEWER ?= claude
DIFF ?= $(AI_REVIEW_ROOT)/tests/fixtures/diffs/simple.diff
REPO ?= $(AI_REVIEW_ROOT)/tests/fixtures/repos/simple
LOCAL_OUT ?= .ai-review-local

.PHONY: test lint review-local consensus-local validate-local

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "import pytest" >/dev/null 2>&1 && PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m pytest $(AI_REVIEW_ROOT)/tests || PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s $(AI_REVIEW_ROOT)/tests -p 'test_*.py'

lint:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "import ruff" >/dev/null 2>&1 && PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ruff check $(AI_REVIEW_ROOT)/src $(AI_REVIEW_ROOT)/tests || PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m compileall -q $(AI_REVIEW_ROOT)/src

review-local:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.input_bundle local --config $(AI_REVIEW_ROOT)/config/review.yaml --diff $(DIFF) --repo $(REPO) --out $(LOCAL_OUT)/inputs
	AI_REVIEW_INPUT_DIR=$(LOCAL_OUT)/inputs AI_REVIEW_OUTPUT_DIR=$(LOCAL_OUT)/out AI_REVIEW_CONFIG=$(AI_REVIEW_ROOT)/config/review.yaml AI_REVIEW_LOCAL_MOCK=1 PYTHONPATH=$(PYTHONPATH) ./$(AI_REVIEW_ROOT)/adapters/run_reviewer.sh $(REVIEWER) review

consensus-local: review-local
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.consensus --config $(AI_REVIEW_ROOT)/config/review.yaml --inputs $(LOCAL_OUT)/inputs --findings-dir $(LOCAL_OUT)/out/findings --out $(LOCAL_OUT)/out/consensus/consensus.json
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.schema validate --schema consensus.schema.json --input $(LOCAL_OUT)/out/consensus/consensus.json

validate-local: review-local
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m ai_review.schema validate --schema finding_batch.schema.json --input $(LOCAL_OUT)/out/findings/$(REVIEWER).json
