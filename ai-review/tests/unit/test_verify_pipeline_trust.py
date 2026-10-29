from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

from tests.support.repository_script import load_repository_script

REPO_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE_TRUST = REPO_ROOT / "scripts" / "pipeline_trust.py"

# The image copies only the handful of scripts it actually executes — the auditor
# is not one of them, by the same reasoning that moved it out of the ai_review
# package. A sparse checkout or archive export can likewise arrive without
# scripts/, so this module skips rather than failing to import, matching
# test_release_tools.py. pytest runs every case below
# under `make quality`, where scripts/ is present.
if not _PIPELINE_TRUST.is_file():
    raise unittest.SkipTest("repository-only trust auditor is absent from the runtime image")


def _load_pipeline_trust():
    """Load the repository-only trust auditor from scripts/.

    It lives outside ``ai_review`` because nothing in the pipeline imports it: it
    audits a *consumer's* .gitlab-ci.yml, and shipping it in the runtime image
    only added surface. Loaded by path so this test does not depend on scripts/
    being on sys.path.
    """
    if "pipeline_trust" in sys.modules:
        return sys.modules["pipeline_trust"]
    return load_repository_script("pipeline_trust", _PIPELINE_TRUST)


_pipeline_trust = _load_pipeline_trust()
RESERVED_JOB_NAMES = _pipeline_trust.RESERVED_JOB_NAMES
find_trust_issues = _pipeline_trust.find_trust_issues

TRUSTED_PROJECT = "org/code-tribunal-ci"
TRUSTED_SHA = "a" * 40
OTHER_SHA = "b" * 40
REVIEW_TEMPLATE = REPO_ROOT / "ai-review/ci/review.gitlab-ci.yml"


def _project_include(file: str, *, project: str = TRUSTED_PROJECT, ref: str = TRUSTED_SHA):
    return {"project": project, "ref": ref, "file": file}


def _direct_config() -> dict:
    return {
        "include": [_project_include("/ai-review/ci/review.gitlab-ci.yml")],
        "stages": ["build", "ai_review", "deploy"],
    }


def _issues(config: dict) -> list[str]:
    return find_trust_issues(
        config,
        expected_template_project=TRUSTED_PROJECT,
        expected_template_sha=TRUSTED_SHA,
    )


def test_flags_expected_project_mismatch() -> None:
    config = _direct_config()
    config["include"][0]["project"] = "attacker/templates"
    assert any("must use trusted project" in issue for issue in _issues(config))


def test_flags_expected_sha_mismatch() -> None:
    config = _direct_config()
    config["include"][0]["ref"] = OTHER_SHA
    assert any("must use trusted commit SHA" in issue for issue in _issues(config))


def test_flags_invalid_expected_trust_root() -> None:
    issues = find_trust_issues(
        _direct_config(),
        expected_template_project=" ",
        expected_template_sha="main",
    )
    assert any("trusted template project must be non-empty" in issue for issue in issues)
    assert any("trusted template ref must be an exact" in issue for issue in issues)


def test_flags_extra_keys_on_project_include() -> None:
    config = _direct_config()
    config["include"][0]["rules"] = [{"when": "always"}]
    assert any("must contain exactly project, ref, and file" in issue for issue in _issues(config))


def test_flags_duplicate_review_includes() -> None:
    config = _direct_config()
    config["include"].append(_project_include("/ai-review/ci/review.gitlab-ci.yml"))
    assert any("exactly once; got 2" in issue for issue in _issues(config))


def test_allows_unrelated_consumer_jobs_and_includes() -> None:
    config = _direct_config()
    config["include"].append({"local": "/ci/tests.yml"})
    config["tests"] = {"stage": "test", "script": ["pytest"]}
    assert _issues(config) == []


def test_allows_exact_direct_include() -> None:
    assert _issues(_direct_config()) == []


def test_flags_unpinned_direct_include() -> None:
    config = _direct_config()
    config["include"][0]["ref"] = "main"
    issues = _issues(config)
    assert any("full 40-character" in issue for issue in issues)


def test_flags_local_direct_template() -> None:
    config = {"include": [{"local": "/ai-review/ci/review.gitlab-ci.yml"}]}
    issues = _issues(config)
    assert any("must include" in issue for issue in issues)


def test_nested_trigger_does_not_satisfy_direct_include() -> None:
    config = {"ai_review": {"trigger": _direct_config()}}
    assert any("exactly once; got 0" in issue for issue in _issues(config))


def test_flags_wrong_direct_template_path() -> None:
    config = {"include": [_project_include("/evil/review.gitlab-ci.yml")]}
    issues = _issues(config)
    assert any("template path must be exact" in issue for issue in issues)


def test_flags_direct_reserved_job_override() -> None:
    config = _direct_config()
    config["post_ai_review"] = {"script": ["exit 0"]}
    issues = _issues(config)
    assert any("must not redefine reserved" in issue for issue in issues)


def test_retired_gate_job_name_is_no_longer_reserved() -> None:
    config = _direct_config()
    config["ai_review_gate"] = {"script": ["exit 0"]}
    assert not _issues(config)


def test_flags_direct_cursor_job_overrides() -> None:
    for job_name in ("AI review: [cursor]", "AI critique: [cursor]"):
        config = _direct_config()
        config[job_name] = {"script": ["exit 0"]}

        issues = _issues(config)

        assert any(job_name in issue for issue in issues)


def test_reserved_jobs_cover_every_shipped_template_job() -> None:
    template = yaml.safe_load(REVIEW_TEMPLATE.read_text(encoding="utf-8"))
    assert isinstance(template, dict)
    shipped_jobs = set(template) - {"variables"}

    assert shipped_jobs <= RESERVED_JOB_NAMES


def test_shipped_gitlab_installation_example_respects_trust_boundaries() -> None:
    path = REPO_ROOT / "docs/getting-started/examples/gitlab-direct.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(config, dict), f"{path}: YAML root must be a mapping"
    issues = find_trust_issues(
        config,
        expected_template_project=TRUSTED_PROJECT,
        expected_template_sha="1" * 40,
    )
    assert issues == [], f"{path}: {issues}"
    template = yaml.safe_load(REVIEW_TEMPLATE.read_text(encoding="utf-8"))
    assert config["workflow"]["rules"] == template[".ai_review_rules"]["rules"]


def test_auditor_cli_accepts_a_trusted_include(tmp_path, capsys) -> None:
    path = tmp_path / ".gitlab-ci.yml"
    path.write_text(yaml.safe_dump(_direct_config()), encoding="utf-8")
    assert _pipeline_trust.main([
        str(path), "--template-project", TRUSTED_PROJECT, "--template-sha", TRUSTED_SHA,
    ]) == 0
    assert "OK: trusted Code Tribunal integration" in capsys.readouterr().out
