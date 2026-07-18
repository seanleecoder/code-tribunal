from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_pyproject_contains_tool_configuration_only() -> None:
    config = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "build-system" not in config
    assert "project" not in config
    assert "setuptools" not in config.get("tool", {})


def test_internal_python_tree_does_not_claim_typed_distribution() -> None:
    package_root = _REPO_ROOT / "ai-review" / "src" / "ai_review"
    init_text = (package_root / "__init__.py").read_text(encoding="utf-8")

    assert not (package_root / "py.typed").exists()
    assert "__version__" not in init_text


def test_container_copies_only_the_internal_package_from_src() -> None:
    dockerfile = (
        _REPO_ROOT / "ai-review" / "images" / "base.Dockerfile"
    ).read_text(encoding="utf-8")

    assert "COPY ai-review/src/ai_review /opt/ai-review/src/ai_review" in dockerfile
    assert "COPY ai-review/src /opt/ai-review/src" not in dockerfile


def test_generated_artifacts_are_excluded_from_git_and_container_contexts() -> None:
    required = {"build/", "dist/", "*.egg-info/", "__pycache__/", ".coverage"}
    gitignore = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    dockerignore = (_REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert required <= set(gitignore)
    assert required <= set(dockerignore)
