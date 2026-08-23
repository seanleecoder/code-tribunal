# CLI modules and exit codes

The Python modules below are internal container entry points invoked by the
supported templates. Their command-line behavior is a product contract; their
importable Python functions are not a public API.

| Module or script | Purpose | Success | Contract failures |
|---|---|---:|---|
| `python -m ai_review.input_bundle local --config PATH --diff PATH --repo PATH --out DIR` | Build a local fixture bundle | 0 | 1 for bundle/config/runtime failure; argparse uses 2 |
| `python -m ai_review.input_bundle prepare --config PATH --out DIR` | Build a platform-bound bundle | 0 | 1 for bundle/platform/config failure; argparse uses 2 |
| `python -m ai_review.adapter_runner REVIEWER STAGE` | Run and validate a reviewer or critique | 0 | 1 after writing a structured failed status; argparse uses 2 |
| `python -m ai_review.consensus --config PATH --inputs DIR --out PATH` | Validate evidence and reduce consensus; findings/critiques/state paths are optional | 0 | 3 for no usable panel or artifact/config integrity failure; argparse uses 2 |
| `python -m ai_review.post --config PATH --inputs DIR --consensus PATH --out PATH` | Publish the review: reconcile state, post threads and the summary; `--dry-run` is optional | 0 for `success` and `stale_head` | 1 for `failed`, `partial_failed`, `state_overflow`, or any unrecognized status, and for unhandled CLI/platform/config failure; argparse uses 2 |
| `python -m ai_review.schema validate --schema NAME --input PATH` | Validate one JSON artifact | 0 | 1 for invalid input/schema; argparse uses 2 |
| `python scripts/pipeline_trust.py PATH --mode MODE --template-project PROJECT --template-sha SHA` | Audit a GitLab consumer composition (`MODE` is `direct` or `child`); repository-only, not shipped in the images | 0 | 1 for trust violations, 2 for malformed input/arguments |
| `python -m ai_review.mock_reviewer REVIEWER STAGE` | Emit deterministic local/preflight review or critique JSON | 0 | argparse uses 2; fixture/artifact errors propagate nonzero |
| `python scripts/check_docs.py` | Audit links, anchors, configuration/environment inventory, and install examples | 0 | 1 when the documentation contract fails |
| `scripts/check_supply_chain_pins.py` | Audit shipped dependency and image pins | 0 | 1 when any pin contract fails |

`post` is the terminal product stage and its exit status is the pipeline's whole
report on the review. It reports **publication**, not findings: a finding of any
severity, `blocker` included, exits zero, and `stale_head` — a newer revision
superseded the run, so no mutation occurred — is a successful no-op. Only
operational failure exits nonzero. `--dry-run` uses the same mapping, so a local
lifecycle run that degrades does not report success. The five statuses above are
the complete `post_result.status` enum today; a status this table does not name
exits nonzero rather than being assumed benign.

Signals and interpreter-level failures may use the host shell's conventional
codes. Consumers should branch only on the documented codes above and should
also inspect the structured artifact where one is produced.

## Common commands

```bash
make quality
make docs-check
make review-local REVIEWER=claude
make consensus-local
make validate-local
```

`make quality` is the sole canonical contributor gate. It runs documentation,
lint, tests with coverage, whole-package typing, supply-chain checks, and Python
compilation without converting an installed checker failure into a fallback
success.
