from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_pipeline_trust.py"
_SPEC = importlib.util.spec_from_file_location("verify_pipeline_trust", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
verify_pipeline_trust = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verify_pipeline_trust)
find_trust_issues = verify_pipeline_trust.find_trust_issues


def test_flags_local_review_include() -> None:
    issues = find_trust_issues({"include": [{"local": "ai-review/ci/review.gitlab-ci.yml"}]})
    assert any("include:local" in issue for issue in issues)


def test_allows_project_include_with_ref() -> None:
    issues = find_trust_issues(
        {
            "include": [
                {
                    "project": "org/code-tribunal-ci",
                    "ref": "v1.0.0",
                    "file": "/review.gitlab-ci.yml",
                }
            ]
        }
    )
    assert issues == []


def test_flags_project_include_without_ref() -> None:
    issues = find_trust_issues(
        {"include": [{"project": "org/code-tribunal-ci", "file": "/review.gitlab-ci.yml"}]}
    )
    assert any("pin ref" in issue for issue in issues)
