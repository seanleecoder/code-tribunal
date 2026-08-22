"""The prepared input bundle an adapter run reads.

``run_adapter`` renders its prompt from a real bundle -- diff, rules, prompts,
manifest, prior decisions, and a repo snapshot carrying every project-level agent
configuration file the adapters are supposed to strip. Two suites need exactly
that shape: the registry-driven reviewer contract in
``unit/test_reviewers.py`` and the provider-specific adapter suite in
``unit/test_openrouter_adapters.py``. It lives here so neither owns it, and so a
new steering file only has to be added in one place to be covered by both.
"""

from __future__ import annotations

from pathlib import Path

from ai_review.schema import write_canonical_json


def write_adapter_input_bundle(input_dir: Path) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "mr.diff").write_text("", encoding="utf-8")
    (input_dir / "config.review.yaml").write_text("reviewers: {}\n", encoding="utf-8")
    (input_dir / "rules").mkdir()
    (input_dir / "rules" / "rule.md").write_text("bundle rule\n", encoding="utf-8")
    (input_dir / "prompts").mkdir()
    (input_dir / "prompts" / "review.md").write_text("bundle prompt\n", encoding="utf-8")
    (input_dir / ".opencode").mkdir()
    (input_dir / ".opencode" / "agent.md").write_text("bundle agent\n", encoding="utf-8")
    repo_snapshot = input_dir / "repo_snapshot"
    repo_snapshot.mkdir()
    (repo_snapshot / "src").mkdir()
    (repo_snapshot / "src" / "reviewed.py").write_text("print('review me')\n", encoding="utf-8")
    (repo_snapshot / "README.md").write_text("# Reviewed project\n", encoding="utf-8")
    (repo_snapshot / "opencode.json").write_text('{"project":true}\n', encoding="utf-8")
    (repo_snapshot / "opencode.jsonc").write_text('{"projectJsonc":true}\n', encoding="utf-8")
    (repo_snapshot / "tui.json").write_text('{"tui":true}\n', encoding="utf-8")
    (repo_snapshot / ".cursorrules").write_text("cursor project rules\n", encoding="utf-8")
    (repo_snapshot / ".cursorignore").write_text("cursor ignore\n", encoding="utf-8")
    (repo_snapshot / "CLAUDE.md").write_text("claude project rules\n", encoding="utf-8")
    (repo_snapshot / ".opencode").mkdir()
    (repo_snapshot / ".opencode" / "plugin.js").write_text(
        "module.exports = {}\n", encoding="utf-8"
    )
    (repo_snapshot / ".cursor").mkdir()
    (repo_snapshot / ".cursor" / "rules.md").write_text("cursor rules\n", encoding="utf-8")
    (repo_snapshot / "AGENTS.md").write_text("project agent instructions\n", encoding="utf-8")
    (repo_snapshot / ".codex").mkdir()
    (repo_snapshot / ".codex" / "config.toml").write_text("[project]\n", encoding="utf-8")
    (repo_snapshot / "nested").mkdir()
    (repo_snapshot / "nested" / "AGENTS.md").write_text(
        "nested agent instructions\n",
        encoding="utf-8",
    )
    (repo_snapshot / "nested" / ".opencode").mkdir()
    (repo_snapshot / "nested" / ".opencode" / "agent.md").write_text(
        "nested agent\n",
        encoding="utf-8",
    )
    (repo_snapshot / "nested" / ".cursor").mkdir()
    (repo_snapshot / "nested" / ".cursor" / "rules.md").write_text(
        "nested cursor rules\n",
        encoding="utf-8",
    )
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
            "config_sha256": "0" * 64,
            "rules_sha256": "0" * 64,
            "created_at": "2026-06-29T00:00:00Z",
        },
    )
    write_canonical_json(
        input_dir / "prior_decisions.json",
        {"schema_version": "prior_decisions.v1", "settled": [], "open": []},
    )
