from __future__ import annotations

from ai_review.pipeline_trust import find_trust_issues


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


def test_flags_local_child_pipeline_include() -> None:
    issues = find_trust_issues(
        {
            "ai_review": {
                "trigger": {
                    "include": [{"local": "/ai-review/ci/review.gitlab-ci.yml"}],
                    "strategy": "mirror",
                }
            }
        }
    )
    assert any("trigger:include:local" in issue for issue in issues)


def test_allows_pinned_project_child_pipeline_include() -> None:
    issues = find_trust_issues(
        {
            "ai_review": {
                "trigger": {
                    "include": [
                        {
                            "project": "org/code-tribunal-ci",
                            "ref": "v0.3.0",
                            "file": "/ai-review/ci/review-child.gitlab-ci.yml",
                        },
                        {
                            "project": "org/code-tribunal-ci",
                            "ref": "v0.3.0",
                            "file": "/ai-review/ci/review.gitlab-ci.yml",
                        },
                    ],
                    "strategy": "mirror",
                }
            }
        }
    )
    assert issues == []


def test_flags_child_wrapper_without_matching_dag() -> None:
    issues = find_trust_issues(
        {
            "ai_review": {
                "trigger": {
                    "include": [
                        {
                            "project": "org/code-tribunal-ci",
                            "ref": "v0.3.0",
                            "file": "/ai-review/ci/review-child.gitlab-ci.yml",
                        }
                    ]
                }
            }
        }
    )
    assert any("same protected project/ref" in issue for issue in issues)


def test_flags_unpinned_project_child_pipeline_include() -> None:
    issues = find_trust_issues(
        {
            "ai_review": {
                "trigger": {
                    "include": [
                        {
                            "project": "org/code-tribunal-ci",
                            "file": "/ai-review/ci/review-child.gitlab-ci.yml",
                        }
                    ]
                }
            }
        }
    )
    assert any("trigger:include:project" in issue and "pin ref" in issue for issue in issues)
