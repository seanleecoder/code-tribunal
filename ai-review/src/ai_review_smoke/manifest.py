"""The declared contents of the packaged smoke suite.

Two kinds of manifest live here, and both are identity checks rather than counts:

* :data:`MANIFEST` names every test ID each image tag must run. The loader builds
  the suite from it and refuses to run when the set it loaded is not equal to it,
  so adding or removing a smoke case requires editing this file in the same
  change. A sentinel case could only prove that *something* ran; it cannot detect
  a case that stopped matching collection -- a renamed method, a class that no
  longer subclasses ``TestCase``, a decorator that swallowed it -- and would
  still exit 0 while publishing an image that quietly lost a check.
* :data:`RUNTIME_MODULES`, :data:`RUNTIME_FILES`, :data:`PACKAGED_FIXTURES`, and
  :data:`PINNED_CLI_VERSION_COMMANDS` name what the image must contain. These
  used to live in workflow shell (``for module in input_bundle consensus post
  schema``), where a deleted module broke image publication and nothing in the
  product suite could see it. The checkout suite asserts these lists against the
  real package, so drift fails ``make quality`` rather than a publish run.
"""

from __future__ import annotations

from ai_review.reviewers import REVIEWERS

# Every module in the shipped ``ai_review`` package. The checkout contract test
# asserts this equals what the package actually contains, in both directions:
# a module added without editing this list fails there, and a module named here
# after deletion fails the in-image import case.
RUNTIME_MODULES: tuple[str, ...] = (
    "ai_review",
    "ai_review.adapter_artifacts",
    "ai_review.adapter_output",
    "ai_review.adapter_process",
    "ai_review.adapter_runner",
    "ai_review.anchors",
    "ai_review.canonical",
    "ai_review.commands",
    "ai_review.config",
    "ai_review.consensus",
    "ai_review.consensus_errors",
    "ai_review.consensus_policy",
    "ai_review.constants",
    "ai_review.critique",
    "ai_review.grouping",
    "ai_review.http_retry",
    "ai_review.input_bundle",
    "ai_review.memory",
    "ai_review.mock_reviewer",
    "ai_review.notes",
    "ai_review.opencode_client",
    "ai_review.platform",
    "ai_review.platform.base",
    "ai_review.platform.github",
    "ai_review.platform.gitlab",
    "ai_review.platform.runtime",
    "ai_review.post",
    "ai_review.posting",
    "ai_review.prompt_render",
    "ai_review.redact",
    "ai_review.render",
    "ai_review.reviewers",
    "ai_review.schema",
    "ai_review.state_plan",
    "ai_review.summary_render",
    "ai_review.types",
)

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
            "adapters/validate_output.py",
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

_BASE_CASES = "ai_review_smoke.base_cases.PackagedBaseImageTests"
_REVIEWER_CASES = "ai_review_smoke.reviewer_cases.PackagedReviewerImageTests"

# The test IDs each scope must run. Keyed by the image tag the preflight targets:
# the properties split across the two tags, because the reviewer cases need the
# pinned CLIs that only the reviewer image has, so one run against one tag cannot
# cover both.
MANIFEST: dict[str, frozenset[str]] = {
    "base": frozenset(
        {
            f"{_BASE_CASES}.test_default_config_loads",
            f"{_BASE_CASES}.test_every_runtime_module_imports",
            f"{_BASE_CASES}.test_expected_runtime_files_exist",
            f"{_BASE_CASES}.test_packaged_cli_entry_points_are_callable",
            f"{_BASE_CASES}.test_packaged_fixtures_exist_where_the_reviewer_preflight_reads_them",
            f"{_BASE_CASES}.test_packaged_runtime_root_is_read_only",
            f"{_BASE_CASES}.test_shipped_schemas_load",
            f"{_BASE_CASES}.test_tmp_is_writable_for_adapter_scratch_space",
        }
    ),
    "reviewer": frozenset(
        {
            f"{_REVIEWER_CASES}.test_every_adapter_script_is_executable",
            f"{_REVIEWER_CASES}.test_local_mock_critique_completes_for_every_seat",
            f"{_REVIEWER_CASES}.test_local_mock_review_validates_every_seats_batch",
            f"{_REVIEWER_CASES}.test_local_consensus_validates_against_its_schema",
            f"{_REVIEWER_CASES}.test_pinned_clis_report_a_version",
        }
    ),
}

# Derived, not declared: every test ID already carries its scope (the key it is
# filed under) and its module (everything before the class), so a second and third
# table naming the same things could only ever drift from this one.
SCOPES: tuple[str, ...] = tuple(MANIFEST)


def scope_case_modules(scope: str) -> frozenset[str]:
    """The modules ``scope``'s declared IDs live in, read back off the IDs."""
    return frozenset(test_id.rsplit(".", 2)[0] for test_id in MANIFEST[scope])
