"""Shared workspace, environment and schema transport for the registry seats."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .adapter_process import _build_adapter_env
from .config import ReviewerConfig
from .reviewers import ReviewerDefinition, get_reviewer_definition
from .schema import load_json_file, schema_dir

_STAGE_SCHEMAS = {
    "review": "raw_finding_batch.schema.json",
    "critique": "critique_batch.schema.json",
}


def stage_schema_path(stage: str) -> Path:
    if (name := _STAGE_SCHEMAS.get(stage)) is None:
        raise ValueError(f"unsupported reviewer stage: {stage!r}")
    return schema_dir() / name


def transport_schema(stage: str) -> dict[str, Any]:
    # Claude and OpenCode reject the repository validator's draft declaration;
    # the rest of the schema is passed unchanged to their structured-output APIs.
    schema: dict[str, Any] = load_json_file(stage_schema_path(stage))
    schema.pop("$schema", None)
    return schema


def state_environment(
    definition: ReviewerDefinition, tmp_dir: Path, values: Mapping[str, str]
) -> dict[str, str]:
    return {
        **{name: str(tmp_dir / directory) for name, directory in definition.state_directories},
        **{name: value.format_map(values) for name, value in definition.environment},
    }


def prepare_workspace(
    definition: ReviewerDefinition, stage: str, input_dir: Path, output_dir: Path
) -> tuple[Path, Path]:
    tmp_dir = (output_dir / ".tmp").resolve()
    root = tmp_dir / f"{definition.reviewer_id}-{stage}-root.{os.getpid()}"
    for directory in (root, *(tmp_dir / name for _, name in definition.state_directories)):
        directory.mkdir(parents=True, exist_ok=True)
    home = tmp_dir / dict(definition.state_directories)["HOME"]
    for path, content in definition.home_files:
        target = home / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    # Review explores a copy of the sanitized pinned snapshot. Critique sees only
    # the prompt's pooled findings, so every seat gets an empty working root.
    if stage == "review":
        snapshot = input_dir / "repo_snapshot"
        if not snapshot.is_dir():
            raise ValueError(
                f"AI review repo_snapshot is required for the {definition.reviewer_id} reviewer"
            )
        shutil.copytree(snapshot, root, symlinks=True, dirs_exist_ok=True)
    return tmp_dir, root


def prepare_shell_adapter(
    definition: ReviewerDefinition, stage: str, env: dict[str, str]
) -> tuple[list[str], Path]:
    """Complete ``env`` for a shell seat; return its CLI arguments and working root."""
    tmp_dir, root = prepare_workspace(
        definition, stage, Path(env["AI_REVIEW_INPUT_DIR"]), Path(env["AI_REVIEW_OUTPUT_DIR"])
    )
    env["AI_REVIEW_RAW_OUTPUT"] = str(tmp_dir / f"{definition.reviewer_id}-{stage}.raw.json")
    values = {
        **env,
        # The adapter itself reports a missing credential; templates must not.
        "OPENROUTER_API_KEY": env.get("OPENROUTER_API_KEY", ""),
        "model": env["AI_REVIEW_MODEL"],
        "root": str(root),
        "schema_path": str(stage_schema_path(stage)),
        "schema_json": json.dumps(transport_schema(stage)),
    }
    env.update(state_environment(definition, tmp_dir, values))
    arguments = definition.cli_arguments + (
        definition.review_arguments if stage == "review" else definition.critique_arguments
    )
    if effort := env.get("AI_REVIEW_EFFORT"):
        values["effort"] = effort
        arguments += definition.effort_arguments
    return [argument.format_map(values) for argument in arguments], root


def main() -> int:
    """Build-time Claude argv/schema acceptance probe, using the runtime setup.

    No credentials exist in the build. Accepted flags still yield a terminal
    result event for the auth error; rejected flags or schemas yield no event.
    This proves acceptance and stream shape, not provider structured output.
    """
    definition = get_reviewer_definition("claude")
    with tempfile.TemporaryDirectory() as scratch:
        work = Path(scratch)
        (work / "inputs" / "repo_snapshot").mkdir(parents=True)
        prompt = work / "probe.md"
        prompt.write_text("probe\n", encoding="utf-8")
        for stage in ("review", "critique"):
            env = _build_adapter_env(
                reviewer="claude", stage=stage, model="claude-haiku-5.5",
                input_dir=work / "inputs", output_dir=work / stage,
                reviewer_config=ReviewerConfig(True, "claude-haiku-5.5", 30, 50, effort="medium"),
                prompt_tmp=prompt,
                reviewer_definition=definition,
            )
            # This probe must exercise the auth-error path even if invoked from
            # a developer shell that holds a real provider credential.
            for name in definition.credential_variables:
                env.pop(name, None)
            arguments, root = prepare_shell_adapter(definition, stage, env)
            result = subprocess.run(
                ["claude", *arguments], input="probe\n", capture_output=True,
                text=True, cwd=root, env=env, check=False, timeout=30,
            )
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "result":
                    break
            else:
                raise RuntimeError(f"Claude {stage} probe failed: {result.stderr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
