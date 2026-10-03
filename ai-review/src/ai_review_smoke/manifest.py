"""Resource and binary inventories for the packaged image smoke checks."""

from __future__ import annotations

from ai_review.reviewers import REVIEWERS

# The modules a consumer pipeline invokes as ``python -m``. Import alone does not
# prove the entry point survived a refactor, so these are additionally required
# to expose a callable ``main``/``cli``.
CLI_MODULES: tuple[str, ...] = (
    "ai_review.input_bundle",
    "ai_review.consensus",
    "ai_review.post",
    "ai_review.schema",
)

# Runtime paths relative to the packaged root (``/opt/ai-review``). Files an
# adapter, a prompt render, or a config load reaches for at review time; a
# missing one is an image packaging bug that no checkout test can see.
# The per-seat adapter scripts are the registry's own ``adapter_path`` values
# rather than a copy of them, so a seat added to ``REVIEWERS`` is required to ship
# here without anyone remembering to extend this list.
RUNTIME_FILES: tuple[str, ...] = tuple(
    sorted(
        {definition.adapter_path for definition in REVIEWERS.values()}
        | {
            "adapters/common.sh",
            "adapters/run_reviewer.sh",
            "config/review.yaml",
            "prompts/critique.md",
            "prompts/review.md",
            "rules/README.md",
        }
    )
)

# The fixture paths the reviewer preflight resolves ``--diff`` and ``--repo``
# from, with no mount. ``base.Dockerfile`` ships them; this is the assertion the
# base preflight used to make as inline ``test -f`` / ``test -d`` shell.
PACKAGED_FIXTURES: tuple[tuple[str, str], ...] = (
    ("tests/fixtures/diffs/simple.diff", "file"),
    ("tests/fixtures/repos/simple", "directory"),
)

# Pinned CLIs the reviewer image installs. Present in the reviewer tag only, so
# these run under the reviewer scope. Each is probed with ``--version``; that was
# a second column here until it was the same string in every row.
PINNED_CLIS: tuple[str, ...] = (
    "claude",
    "codex",
    "opencode",
    "cursor-agent",
    "rg",
)
