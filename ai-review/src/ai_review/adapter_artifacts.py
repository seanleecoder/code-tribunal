"""Status and debug artifact writing.

Every filesystem write the adapter runner makes outside the reviewer batch
itself: status artifacts, empty batches, and the parse-failure debug pair.

Keeping these out of adapter_output is the point of the split — it is what
stops filesystem I/O leaking into the parsing module.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .adapter_output import _head_tail_preview
from .anchors import is_sha256
from .config import ConfigError, effective_config_digest
from .redact import redact_text
from .schema import (
    adapter_status_artifact,
    empty_critique_batch,
    empty_finding_batch,
    load_json_file,
    now_iso,
    validate_instance,
    write_canonical_json,
)

_RAW_STDOUT_ARTIFACT_LIMIT = 2 * 1024 * 1024


def _manifest_run_id(input_dir: Path) -> str:
    manifest_path = input_dir / "manifest.json"
    if manifest_path.exists():
        manifest = load_json_file(manifest_path)
        if isinstance(manifest, dict) and manifest.get("run_id"):
            return str(manifest["run_id"])
    return "unknown-run"


def _manifest_effective_config_sha256(input_dir: Path) -> str | None:
    manifest_path = input_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = load_json_file(manifest_path)
    if isinstance(manifest, dict):
        digest = manifest.get("effective_config_sha256")
        if is_sha256(digest):
            return str(digest)
    return None


def _resolve_config_digest(input_dir: Path, config: dict[str, Any] | None = None) -> str:
    """Prefer the live effective digest; fall back to the prepare manifest stamp."""
    if config is not None:
        return effective_config_digest(config)
    digest = _manifest_effective_config_sha256(input_dir)
    if digest is None:
        raise ConfigError(
            "effective_config_sha256 unavailable: load config or re-run prepare "
            "so the manifest records the digest"
        )
    return digest


def _output_file(stage: str, reviewer: str) -> Path:
    if stage == "review":
        return Path("findings") / f"{reviewer}.json"
    return Path("critiques") / f"{reviewer}.json"


def _status_stem(stage: str, reviewer: str) -> str:
    if stage == "critique":
        return f"critique-{reviewer}"
    return reviewer


def _write_status(
    output_dir: Path,
    reviewer: str,
    stage: str,
    status: str,
    started_at: str,
    started_monotonic: float,
    output_file: Path,
    *,
    error_class: str | None = None,
    error_message: str | None = None,
    run_id: str | None = None,
    effective_config_sha256: str | None = None,
    raw_finding_count: int | None = None,
    accepted_finding_count: int | None = None,
    dropped_finding_count: int | None = None,
    usable_for_resolution: bool | None = None,
) -> None:
    completed = now_iso()
    artifact = adapter_status_artifact(
        reviewer,
        stage,
        status,
        started_at,
        completed,
        int((time.monotonic() - started_monotonic) * 1000),
        output_file.as_posix(),
        error_class=error_class,
        error_message_redacted=redact_text(error_message) if error_message else None,
        run_id=run_id,
        effective_config_sha256=effective_config_sha256,
        raw_finding_count=raw_finding_count,
        accepted_finding_count=accepted_finding_count,
        dropped_finding_count=dropped_finding_count,
        usable_for_resolution=usable_for_resolution,
    )
    validate_instance(artifact, "adapter_status.schema.json")
    write_canonical_json(output_dir / "status" / f"{_status_stem(stage, reviewer)}.json", artifact)


def _write_empty(
    output_dir: Path,
    output_file: Path,
    reviewer: str,
    stage: str,
    status: str,
    run_id: str,
    model: str,
    started_at: str,
    *,
    effective_config_sha256: str,
) -> None:
    if stage == "review":
        batch = empty_finding_batch(
            reviewer,
            status,
            run_id=run_id,
            model=model,
            started_at=started_at,
            effective_config_sha256=effective_config_sha256,
        )
        validate_instance(batch, "finding_batch.schema.json")
    else:
        batch = empty_critique_batch(
            reviewer,
            status,
            run_id=run_id,
            started_at=started_at,
            effective_config_sha256=effective_config_sha256,
        )
        validate_instance(batch, "critique_batch.schema.json")
    write_canonical_json(output_dir / output_file, batch)


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
    stem = _status_stem(stage, reviewer)
    debug_path = output_dir / "status" / f"{stem}-{kind}-debug.txt"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(
        "\n".join(
            [
                "stdout_preview:",
                redact_text(_head_tail_preview(stdout, limit=4000)),
                "",
                "stderr_preview:",
                redact_text(_head_tail_preview(stderr, limit=4000)),
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
    raw_text = redact_text(stdout)
    if len(raw_text) > _RAW_STDOUT_ARTIFACT_LIMIT:
        raw_text = (
            raw_text[:_RAW_STDOUT_ARTIFACT_LIMIT]
            + f"\n...[truncated at {_RAW_STDOUT_ARTIFACT_LIMIT} characters]...\n"
        )
    raw_path.write_text(raw_text, encoding="utf-8")
