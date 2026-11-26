"""The prepared input bundle an adapter run reads.

``run_adapter`` renders its prompt from a real bundle -- diff, rules, prompts,
manifest, prior decisions, and a repo snapshot as prepare leaves it, with the
reviewer agent files already dropped. Several adapter suites need exactly that
shape, so it lives here and none of them owns it.

``run_prepared_adapter`` is shared more widely: every adapter-runner suite that
writes its own manifest needs prepare's effective-config binding in it.
``run_captured_adapter`` replays one captured CLI result through the runner.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from ai_review.adapter_process import _AdapterResult
from ai_review.adapter_runner import run_adapter
from ai_review.config import ConfigError, effective_config_binding, load_config
from ai_review.reviewers import CLAUDE_REPOSITORY_TOOLS
from ai_review.schema import load_json_file, write_canonical_json

# The pinned Claude CLI's init event. A review answer is admitted only after it.
CLAUDE_REVIEW_INIT = json.dumps(
    {"type": "system", "subtype": "init", "tools": [*CLAUDE_REPOSITORY_TOOLS, "StructuredOutput"]}
)


def bind_manifest_to_config(
    input_dir: Path, config_path: Path, *, findings_dir: Path | None = None
) -> None:
    """Record the effective config in the manifest, as prepare does.

    This simulates trusted prepare for tests that construct a bundle directly.
    The runner consumes the recorded settings without reading this YAML again.
    An invalid document is left unbound, so runtime admission reports missing
    prepared configuration.
    """
    manifest_path = input_dir / "manifest.json"
    try:
        config = load_config(config_path)
    except ConfigError:
        return
    if not manifest_path.exists():
        return
    manifest = load_json_file(manifest_path)
    manifest.update(effective_config_binding(config))
    write_canonical_json(manifest_path, manifest)
    if findings_dir is not None:
        for path in findings_dir.glob("*.json"):
            batch = load_json_file(path)
            batch["effective_config_sha256"] = manifest["effective_config_sha256"]
            write_canonical_json(path, batch)


def run_prepared_adapter(reviewer: str, stage: str) -> int:
    """``run_adapter`` with the manifest bound to the current environment."""
    bind_manifest_to_config(
        Path(os.environ.get("AI_REVIEW_INPUT_DIR", "inputs")),
        Path(os.environ.get("AI_REVIEW_CONFIG", "config/review.yaml")),
    )
    return run_adapter(reviewer, stage)


def run_captured_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reviewer: str,
    result: _AdapterResult,
    config_path: Path,
) -> tuple[int, dict[str, Any]]:
    """Run a review on a fresh bundle with ``result`` as the CLI's; return exit and outcome."""
    inputs, output = tmp_path / "inputs", tmp_path / "out"
    write_adapter_input_bundle(inputs)
    bind_manifest_to_config(inputs, config_path)
    monkeypatch.setenv("AI_REVIEW_INPUT_DIR", str(inputs))
    monkeypatch.setenv("AI_REVIEW_OUTPUT_DIR", str(output))
    with mock.patch("ai_review.adapter_runner._run_adapter_process", return_value=result):
        code = run_adapter(reviewer, "review")
    return code, json.loads((output / f"findings/{reviewer}.json").read_text())


def write_adapter_input_bundle(input_dir: Path) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "mr.diff").write_text("", encoding="utf-8")
    (input_dir / ".opencode").mkdir()
    (input_dir / ".opencode" / "agent.md").write_text("bundle agent\n", encoding="utf-8")
    repo_snapshot = input_dir / "repo_snapshot"
    repo_snapshot.mkdir()
    (repo_snapshot / "src").mkdir()
    (repo_snapshot / "src" / "reviewed.py").write_text("print('review me')\n", encoding="utf-8")
    (repo_snapshot / "README.md").write_text("# Reviewed project\n", encoding="utf-8")
    write_canonical_json(
        input_dir / "manifest.json",
        {
            "schema_version": "input_manifest.v1",
            "run_id": "local-test",
            "project_id": "local",
            "project_path": "local/project",
            "merge_request_iid": "1",
            "source_branch": "s",
            "target_branch": "t",
            "base_sha": "0" * 40,
            "start_sha": "0" * 40,
            "head_sha": "1" * 40,
            "created_at": "2026-06-29T00:00:00Z",
        },
    )
    write_canonical_json(
        input_dir / "prior_decisions.json",
        {"schema_version": "prior_decisions.v1", "settled": [], "open": []},
    )
