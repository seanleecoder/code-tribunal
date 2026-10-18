"""The prepared input bundle an adapter run reads.

``run_adapter`` renders its prompt from a real bundle -- diff, rules, prompts,
manifest, prior decisions, and a repo snapshot as prepare leaves it, with the
reviewer agent files already dropped. Two suites need exactly that shape: the
registry-driven reviewer contract in ``unit/test_reviewers.py`` and the
provider-specific adapter suite in ``unit/test_openrouter_adapters.py``. It
lives here so neither owns it.

``run_prepared_adapter`` is shared more widely: every adapter-runner suite that
writes its own manifest needs prepare's effective-config binding in it.
"""

from __future__ import annotations

import os
from pathlib import Path

from ai_review.adapter_runner import run_adapter
from ai_review.config import ConfigError, effective_config_binding, load_config
from ai_review.schema import load_json_file, write_canonical_json


def bind_manifest_to_config(input_dir: Path, config_path: Path) -> None:
    """Record the effective config in the manifest, as prepare does.

    The runner refuses to run when its effective config differs from the
    manifest's, so a hand-written manifest is bound under the same ``AI_REVIEW_*``
    environment the runner will see. An invalid config is left unbound: the
    runner reports it itself.
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


def run_prepared_adapter(reviewer: str, stage: str) -> int:
    """``run_adapter`` with the manifest bound to the current environment."""
    bind_manifest_to_config(
        Path(os.environ.get("AI_REVIEW_INPUT_DIR", "inputs")),
        Path(os.environ.get("AI_REVIEW_CONFIG", "config/review.yaml")),
    )
    return run_adapter(reviewer, stage)


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
            "diff_sha256": "0" * 64,
            "repo_snapshot_sha256": "0" * 64,
            "created_at": "2026-06-29T00:00:00Z",
        },
    )
    write_canonical_json(
        input_dir / "prior_decisions.json",
        {"schema_version": "prior_decisions.v1", "settled": [], "open": []},
    )
