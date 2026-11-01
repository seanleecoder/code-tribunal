"""Redacted parse and timeout diagnostics, never authoritative evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapter_output import _head_tail_preview
from .redact import redact_text
from .schema import load_json_file

_RAW_STDOUT_ARTIFACT_LIMIT = 2 * 1024 * 1024


def _load_manifest(input_dir: Path) -> dict[str, Any]:
    """The prepare manifest, or an empty mapping when there is none."""
    manifest_path = input_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    manifest = load_json_file(manifest_path)
    return manifest if isinstance(manifest, dict) else {}


def _output_file(stage: str, reviewer: str) -> Path:
    if stage == "review":
        return Path("findings") / f"{reviewer}.json"
    return Path("critiques") / f"{reviewer}.json"


def _write_parse_debug(
    output_dir: Path,
    reviewer: str,
    stage: str,
    stdout: str | bytes,
    stderr: str | bytes,
    *,
    kind: str = "parse",
) -> None:
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    # Redact complete spans before a preview or artifact limit can split them.
    stdout = redact_text(stdout)
    stderr = redact_text(stderr)
    stem = f"critique-{reviewer}" if stage == "critique" else reviewer
    debug_path = output_dir / "status" / f"{stem}-{kind}-debug.txt"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(
        "\n".join(
            [
                "stdout_preview:",
                _head_tail_preview(stdout, limit=4000),
                "",
                "stderr_preview:",
                _head_tail_preview(stderr, limit=4000),
                "",
            ]
        ),
        encoding="utf-8",
    )
    # The 4 KB preview elides the middle, which for a stream adapter is where the
    # assistant parts live — reconstructing GitLab job 2624957 required guessing
    # at the elided region. Keep the whole thing next to the preview, bounded and
    # redacted, so the next parse failure is readable instead of inferred.
    raw_path = output_dir / "status" / f"{stem}-{kind}-raw-stdout.txt"
    if len(stdout) > _RAW_STDOUT_ARTIFACT_LIMIT:
        stdout = (
            stdout[:_RAW_STDOUT_ARTIFACT_LIMIT]
            + f"\n...[truncated at {_RAW_STDOUT_ARTIFACT_LIMIT} characters]...\n"
        )
    raw_path.write_text(stdout, encoding="utf-8")
