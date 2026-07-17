from __future__ import annotations

from copy import deepcopy

from ai_review.pipeline_trust import find_trust_issues

TRUSTED_PROJECT = "org/code-tribunal-ci"
TRUSTED_SHA = "a" * 40
OTHER_SHA = "b" * 40


def _project_include(file: str, *, project: str = TRUSTED_PROJECT, ref: str = TRUSTED_SHA):
    return {"project": project, "ref": ref, "file": file}


def _child_config() -> dict:
    return {
        "ai_review": {
            "stage": "ai_review",
            "needs": [],
            "inherit": {"variables": False},
            "trigger": {
                "include": [
                    _project_include("/ai-review/ci/review-child.gitlab-ci.yml"),
                    _project_include("/ai-review/ci/review.gitlab-ci.yml"),
                ],
                "strategy": "mirror",
                "forward": {
                    "yaml_variables": False,
                    "pipeline_variables": False,
                },
            },
        }
    }


def _direct_config() -> dict:
    return {
        "include": [_project_include("/ai-review/ci/review.gitlab-ci.yml")],
        "stages": ["build", "ai_review", "deploy"],
    }


def _issues(config: dict, *, mode: str = "child") -> list[str]:
    return find_trust_issues(
        config,
        mode=mode,  # type: ignore[arg-type]
        expected_template_project=TRUSTED_PROJECT,
        expected_template_sha=TRUSTED_SHA,
    )


def test_allows_exact_child_bundle() -> None:
    assert _issues(_child_config()) == []


def test_flags_child_wrapper_without_dag() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"].pop()
    issues = _issues(config)
    assert any("exactly two" in issue for issue in issues)
    assert any("review.gitlab-ci.yml' exactly once; got 0" in issue for issue in issues)


def test_flags_child_wrapper_and_dag_with_different_refs() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"][1]["ref"] = OTHER_SHA
    issues = _issues(config)
    assert any("must use trusted commit SHA" in issue for issue in issues)


def test_flags_child_wrapper_and_dag_with_different_projects() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"][1]["project"] = "attacker/templates"
    issues = _issues(config)
    assert any("must use trusted project" in issue for issue in issues)


def test_flags_expected_project_mismatch() -> None:
    config = _child_config()
    issues = find_trust_issues(
        config,
        mode="child",
        expected_template_project="different/trusted-project",
        expected_template_sha=TRUSTED_SHA,
    )
    assert sum("must use trusted project" in issue for issue in issues) == 2


def test_flags_expected_sha_mismatch() -> None:
    config = _child_config()
    issues = find_trust_issues(
        config,
        mode="child",
        expected_template_project=TRUSTED_PROJECT,
        expected_template_sha=OTHER_SHA,
    )
    assert sum("must use trusted commit SHA" in issue for issue in issues) == 2


def test_flags_branch_or_tag_ref() -> None:
    config = _child_config()
    for entry in config["ai_review"]["trigger"]["include"]:
        entry["ref"] = "v0.3.0"
    issues = _issues(config)
    assert sum("full 40-character" in issue for issue in issues) == 2


def test_flags_invalid_expected_sha() -> None:
    issues = find_trust_issues(
        _child_config(),
        mode="child",
        expected_template_project=TRUSTED_PROJECT,
        expected_template_sha="main",
    )
    assert any("trusted template ref must be an exact" in issue for issue in issues)


def test_flags_arbitrary_local_child_include() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"].append({"local": "ci/evil.yml"})
    issues = _issues(config)
    assert any("exactly two" in issue for issue in issues)
    assert any("forbidden include kind" in issue for issue in issues)


def test_flags_remote_child_include() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"][1] = {"remote": "https://attacker.example/review.yml"}
    issues = _issues(config)
    assert any("forbidden include kind" in issue for issue in issues)


def test_flags_component_child_include() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"][1] = {
        "component": "attacker.example/components/review@main"
    }
    issues = _issues(config)
    assert any("forbidden include kind" in issue for issue in issues)


def test_flags_string_child_include() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"][1] = "ci/evil.yml"
    issues = _issues(config)
    assert any("string, local, remote" in issue for issue in issues)


def test_flags_extra_project_child_include() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"].append(
        _project_include("/ai-review/ci/extra.gitlab-ci.yml")
    )
    issues = _issues(config)
    assert any("exactly two" in issue for issue in issues)
    assert any("file must be exactly" in issue for issue in issues)


def test_flags_duplicate_child_wrapper() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"][1] = deepcopy(
        config["ai_review"]["trigger"]["include"][0]
    )
    issues = _issues(config)
    assert any("review-child.gitlab-ci.yml' exactly once; got 2" in issue for issue in issues)
    assert any("review.gitlab-ci.yml' exactly once; got 0" in issue for issue in issues)


def test_flags_correct_basename_at_wrong_path() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"][1]["file"] = "/evil/review.gitlab-ci.yml"
    issues = _issues(config)
    assert any("file must be exactly" in issue for issue in issues)


def test_flags_extra_keys_on_project_include() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["include"][1]["rules"] = [{"when": "always"}]
    issues = _issues(config)
    assert any("must contain exactly project, ref, and file" in issue for issue in issues)


def test_flags_missing_mirror_strategy() -> None:
    config = _child_config()
    config["ai_review"]["trigger"].pop("strategy")
    issues = _issues(config)
    assert any("trigger.strategy must be 'mirror'" in issue for issue in issues)


def test_flags_missing_variable_inheritance_boundary() -> None:
    config = _child_config()
    config["ai_review"].pop("inherit")
    issues = _issues(config)
    assert any("inherit:variables to false" in issue for issue in issues)


def test_allows_other_safe_inheritance_controls() -> None:
    config = _child_config()
    config["ai_review"]["inherit"]["default"] = False
    assert _issues(config) == []


def test_flags_bridge_variables_even_when_forwarding_is_disabled() -> None:
    config = _child_config()
    config["ai_review"]["variables"] = {"AI_REVIEW_REVIEWER_IMAGE": "attacker/evil:latest"}
    issues = _issues(config)
    assert any("must not define bridge variables" in issue for issue in issues)


def test_flags_omitted_forward_block_because_yaml_forwarding_defaults_true() -> None:
    config = _child_config()
    config["ai_review"]["trigger"].pop("forward")
    issues = _issues(config)
    assert any("trigger.forward must explicitly disable" in issue for issue in issues)


def test_flags_yaml_variable_forwarding() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["forward"]["yaml_variables"] = True
    issues = _issues(config)
    assert any("trigger.forward must explicitly disable" in issue for issue in issues)


def test_flags_pipeline_variable_forwarding() -> None:
    config = _child_config()
    config["ai_review"]["trigger"]["forward"]["pipeline_variables"] = True
    issues = _issues(config)
    assert any("trigger.forward must explicitly disable" in issue for issue in issues)


def test_allows_parent_variables_when_bridge_is_isolated() -> None:
    config = _child_config()
    config["variables"] = {
        "AI_REVIEW_REVIEWER_IMAGE": "attacker/evil:latest",
        "AI_REVIEW_LOCAL_MOCK": "1",
    }
    assert _issues(config) == []


def test_flags_missing_child_bridge() -> None:
    assert any("requires an ai_review bridge job" in issue for issue in _issues({}))


def test_allows_exact_direct_include() -> None:
    assert _issues(_direct_config(), mode="direct") == []


def test_flags_unpinned_direct_include() -> None:
    config = _direct_config()
    config["include"][0]["ref"] = "main"
    issues = _issues(config, mode="direct")
    assert any("full 40-character" in issue for issue in issues)


def test_flags_local_direct_template() -> None:
    config = {"include": [{"local": "/ai-review/ci/review.gitlab-ci.yml"}]}
    issues = _issues(config, mode="direct")
    assert any("must include" in issue for issue in issues)


def test_flags_wrong_direct_template_path() -> None:
    config = {"include": [_project_include("/evil/review.gitlab-ci.yml")]}
    issues = _issues(config, mode="direct")
    assert any("template path must be exact" in issue for issue in issues)


def test_flags_direct_reserved_job_override() -> None:
    config = _direct_config()
    config["ai_review_gate"] = {"script": ["exit 0"]}
    issues = _issues(config, mode="direct")
    assert any("must not redefine reserved" in issue for issue in issues)
