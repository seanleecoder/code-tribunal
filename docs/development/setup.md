# Contributor setup

Use Python 3.14 from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
export PYTHONPATH="$PWD/ai-review/src"
.venv/bin/python scripts/check_markdown_links.py install \
  --cache-dir .venv/lychee-cache --bin-dir .venv/bin
export PATH="$PWD/.venv/bin:$PATH"
make quality
```

`make quality` is the same blocking command used by repository CI. It runs the
Markdown link checks, Ruff, pytest with coverage, whole-package mypy,
supply-chain validation, and zizmor's workflow pin audits.
The Lychee installer selects the reviewed native archive for Linux x86-64 or
macOS Intel/Apple Silicon and verifies its pinned SHA-256 before placing it on
the local `PATH`; the gate never compiles or silently skips the tool.

`make` selects the checkout's `.venv/bin/python` when present. Override `PYTHON`
with an **absolute** project interpreter when checking a disposable source copy.
The Ruff and mypy gates check executed versions against `requirements-dev.txt`
and print interpreter, module and executable provenance; metadata alone can lie.

### Termux development profile

The commands below run directly on Termux. If your local environment provides
an `agent-check` wrapper, you can optionally prefix commands with `agent-check --`.
That wrapper is external to this repository and repairs only the child runtime;
it does not install dependencies or provide Linux/Android parity. Test installs
in a disposable venv first; do not overwrite an existing environment with system
packages enabled.

Ruff's ordinary Android source build requires a native Rust toolchain. On
Termux aarch64 this explicit setup downloads the same pinned upstream static
musllinux executable and Python shim from PyPI, verifies the release archive's
SHA-256 and probes the actual executable. No quality gate downloads tools and no
incompatible wheel metadata is installed:

```bash
.venv/bin/python scripts/install_termux_ruff.py
make lint
```

The pinned mypy 2.4.0 needs `ast-serialize>=0.11.2,<1`. No Android wheel was
available in the audited release; building it requires Rust/maturin. A fresh
requirements install remains blocked without native build prerequisites; other
native dependencies can need them too. Do not call copied packages a clean
bootstrap or hide missing dependencies with `--no-deps` or a false `pip check`.

If mypy itself is already provisioned, this explicit developer profile keeps
strict checking and selects its Python parser:

```bash
make typecheck MYPY_FLAGS=--no-native-parser
make quality MYPY_FLAGS=--no-native-parser
```

COMPAT-011 is not native parser or CI parity. CI keeps `MYPY_FLAGS` empty. Report
the missing parser dependency with local successes. The reviewed Lychee archive
installer supports no Android target: provision native Lychee 0.24.2 (checked
against `ai-review/images/lychee.pin`) separately. Put the zizmor 1.31.1 static
musllinux executable in `.venv/bin` or `~/.local/bin`, then expose those paths
explicitly in noninteractive shells:

```bash
export PATH="$PWD/.venv/bin:$HOME/.local/bin:$PATH"
lychee --version
zizmor --version
```

The versions must match the existing Lychee pin and `requirements-dev.txt`.
Offline zizmor still lacks CI's authenticated `ref-version-mismatch` audit.
Neither prerequisite may be silently skipped.

### Focused commands

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
on `PATH`, installed as shown in the first code block, and so must `zizmor`.
`requirements-dev.txt` installs it everywhere except Android, where PyPI has no
wheel; there, put the `zizmor` binary from its `musllinux` wheel on `PATH`.
Without `GH_TOKEN`, zizmor runs offline and skips `ref-version-mismatch`, which
CI runs with a token. Ruff parses the Python
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
