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
make packaged-smoke SCOPE=base
```

`make test` runs pytest, which is the only supported test command: parts of the
suite are pytest-style functions that `unittest` cannot collect, so there is no
fallback runner to drop back to. Without pytest installed the target fails and
names `requirements-dev.txt` rather than running a weaker subset.

`make packaged-smoke` runs the curated packaged-runtime smoke suite that ships in
the published images, by the same module name the image preflight uses.
`SCOPE=base` covers the runtime files, fixtures, module imports, schemas, and
default config; `SCOPE=reviewer` additionally drives every seat's local mock
review, critique, and consensus run and needs the pinned CLIs, so it is fully
green only inside the reviewer image. It is not part of `make quality`, whose
test gate is the checkout pytest suite above.

Local harness output defaults to `.ai-review-local/`; set `LOCAL_OUT` to keep it
elsewhere. Mock mode requires no provider credentials.
