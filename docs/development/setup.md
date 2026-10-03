# Contributor setup

Use Python 3.14 from the repository root:

```bash
python3 -m pip install -r requirements-dev.txt
export PYTHONPATH="$PWD/ai-review/src"
python3 scripts/check_markdown_links.py install \
  --cache-dir .venv/lychee-cache --bin-dir .venv/bin
export PATH="$PWD/.venv/bin:$PATH"
make quality
```

`make quality` is the same blocking command used by repository CI. It runs the
Markdown link checks, Ruff, pytest with coverage, whole-package mypy,
supply-chain validation, release-input checks, and workflow parity.
The Lychee installer selects the reviewed native archive for Linux x86-64 or
macOS Intel/Apple Silicon and verifies its pinned SHA-256 before placing it on
the local `PATH`; the gate never compiles or silently skips the tool.

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
fallback runner. Install the pinned development dependencies from
`requirements-dev.txt` before running it. The pinned Lychee binary must also be
on `PATH`, installed as shown in the first code block. Ruff parses the Python
sources during linting; image builds retain their compilation pass.

`make packaged-smoke` runs the curated packaged-runtime smoke suite that ships in
the published images, by the same module name the image preflight uses. Each scope uses standard
`unittest` module loading, rejects loading failures and zero collected tests, and
collects added or renamed test methods without a separate test-ID inventory.
`SCOPE=base` covers the runtime files, fixtures, module imports, schemas, and
default config; `SCOPE=reviewer` additionally drives every seat's local mock
review, critique, and consensus run and needs the pinned CLIs, so it is fully
green only inside the reviewer image. It is not part of `make quality`, whose
test gate is the checkout pytest suite above.

Local harness output defaults to `.ai-review-local/`; set `LOCAL_OUT` to keep it
elsewhere. Mock mode requires no provider credentials.
