# CLI modules and exit codes

The Python modules below are internal container entry points invoked by the
supported templates. Their command-line behavior is a product contract; their
importable Python functions are not a public API.

| Module or script | Purpose | Success | Contract failures |
|---|---|---:|---|
| `python -m ai_review.input_bundle local` | Build a local fixture bundle | 0 | 1 for bundle/config/runtime failure; argparse uses 2 |
| `python -m ai_review.input_bundle prepare` | Build a platform-bound bundle | 0 | 1 for bundle/platform/config failure; argparse uses 2 |
| `python -m ai_review.adapter_runner REVIEWER STAGE` | Run and validate a reviewer or critique | 0 | 1 after writing a structured failed status; argparse uses 2 |
| `python -m ai_review.prompt_render` | Render review/critique prompts | 0 | 1 for render/config/artifact failure; argparse uses 2 |
| `python -m ai_review.consensus` | Validate evidence and reduce consensus | 0 | 3 for no usable panel or artifact/config integrity failure; argparse uses 2 |
| `python -m ai_review.post` | Reconcile state and post results | 0 | 1 for unhandled CLI/platform/config failure; operational outcomes are recorded in `post_result.json` for the gate |
| `python -m ai_review.gate` | Evaluate operational and finding gates | 0 | 7 for failed post/state or blocking findings; argparse uses 2 |
| `python -m ai_review.schema validate` | Validate one JSON artifact | 0 | 1 for invalid input/schema; argparse uses 2 |
| `scripts/verify_pipeline_trust.py` | Audit a GitLab consumer composition | 0 | 1 for trust violations, 2 for malformed input/arguments |
| `scripts/check_supply_chain_pins.py` | Audit shipped dependency and image pins | 0 | 1 when any pin contract fails |

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
