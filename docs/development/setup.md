# Contributor setup

Use Python 3.12 from the repository root:

```bash
python3 -m pip install -r requirements-dev.txt
export PYTHONPATH="$PWD/ai-review/src"
make quality
```

`make quality` is the same blocking command used by repository CI. It runs the
documentation contract checks, Ruff, pytest with coverage, whole-package mypy,
supply-chain validation, and compilation.

Useful focused commands:

```bash
make docs-check
make test
make lint
make typecheck
make review-local REVIEWER=claude
make consensus-local
```

Local harness output defaults to `.ai-review-local/`; set `LOCAL_OUT` to keep it
elsewhere. Mock mode requires no provider credentials.
