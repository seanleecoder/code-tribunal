PYTHON ?= python3
AI_REVIEW_ROOT ?= ai-review
PYTHONPATH := $(AI_REVIEW_ROOT)/src
REVIEWER ?= claude
DIFF ?= $(AI_REVIEW_ROOT)/tests/fixtures/diffs/simple.diff
REPO ?= $(AI_REVIEW_ROOT)/tests/fixtures/repos/simple
LOCAL_OUT ?= .ai-review-local
RELEASE_OUT ?= /tmp/code-tribunal-release
SCOPE ?= base
RUFF_PATHS := $(AI_REVIEW_ROOT)/src $(AI_REVIEW_ROOT)/tests scripts
PYTEST_ARGS := $(AI_REVIEW_ROOT)/tests --cov=ai_review --cov-report=term-missing

.PHONY: quality test packaged-smoke lint typecheck supply-chain \
	release-inputs docs-check sync-workflows workflow-parity demo-preflight evidence-records \
	release-repin release-finalize release-manifest release-open-next \
	update-golden review-local consensus-local validate-local

quality: docs-check lint test typecheck supply-chain release-inputs workflow-parity

# The single gate on canonical-template -> installed-copy parity;
# `make sync-workflows` repairs the drift it reports.
workflow-parity:
	$(MAKE) --no-print-directory CHECK=1 sync-workflows

docs-check:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/check_markdown_links.py

test:
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

supply-chain:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/check_supply_chain_pins.py

release-inputs:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) scripts/check_release_inputs.py

# Read-only check of both demo consumers before a live campaign. Needs network
# and authenticated gh and glab, so it is not part of `quality`.
demo-preflight:
	PYTHONPATH=$(PYTHONPATH):scripts $(PYTHON) scripts/demo_preflight.py

# Rewrite the release evidence records from one Candidate Canary run:
#   make evidence-records RUN=<run id>
evidence-records:
	@test -n "$(RUN)" || { echo "usage: make evidence-records RUN=<canary run id>"; exit 2; }
	PYTHONPATH=$(PYTHONPATH):scripts $(PYTHON) scripts/canary_evidence_records.py $(RUN)

release-repin:
	@test -n "$(RUN)" || { echo "usage: make release-repin RUN=<canary run id>"; exit 2; }
	PYTHONPATH=$(PYTHONPATH):scripts $(PYTHON) scripts/release_finalize.py repin --run "$(RUN)"

release-finalize:
	@test -n "$(RUN)" -a -n "$(EVIDENCE)" || { echo "usage: make release-finalize RUN=<run> EVIDENCE='<record IDs>' [WAIVE='<quoted RECORD=REASON arguments>']"; exit 2; }
	PYTHONPATH=$(PYTHONPATH):scripts $(PYTHON) scripts/release_finalize.py finalize --run "$(RUN)" --evidence $(EVIDENCE) $(if $(WAIVE),--waive $(WAIVE),)

release-manifest:
	@test -n "$(P)" || { echo "usage: make release-manifest P=<release commit>"; exit 2; }
	PYTHONPATH=$(PYTHONPATH):scripts $(PYTHON) scripts/release_finalize.py manifest --release-commit "$(P)" --out "$(RELEASE_OUT)"

release-open-next:
	@test -n "$(V)" || { echo "usage: make release-open-next V=<next version>"; exit 2; }
	PYTHONPATH=$(PYTHONPATH):scripts $(PYTHON) scripts/release_finalize.py open-next --version "$(V)"

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
