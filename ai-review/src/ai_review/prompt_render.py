from __future__ import annotations

from pathlib import Path
from typing import Any

from .canonical import canonical_json_text
from .config import ReviewConfig
from .reviewers import trusted_runtime_root
from .schema import load_json_file, write_canonical_json


class PromptRenderError(ValueError):
    pass


def _read_rules() -> str:
    rules_dir = trusted_runtime_root() / "rules"
    if not rules_dir.exists():
        return ""
    chunks: list[str] = []
    for path in sorted(rules_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(rules_dir).as_posix()
            chunks.append(f"### {rel}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(chunks)


def _prompt_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """The manifest as reviewers see it, without the effective config.

    The config binds stages to each other; a reviewer gains nothing from it, and
    it would cost prompt bytes on every provider call.
    """
    return {key: value for key, value in manifest.items() if key != "effective_config"}


def _diff_stats_text(diff_text: str) -> str:
    # Approximate size summary so reviewers can calibrate exploration depth
    # (referenced by prompts/review.md). Calibration hint, not ground truth: a
    # deleted line whose own content begins with "--" is skipped by the `---`
    # exclusion.
    files = insertions = deletions = 0
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return f"files_changed: {files}\ninsertions: {insertions}\ndeletions: {deletions}"


def _render(stage: str, config: ReviewConfig, blocks: list[tuple[str, str]]) -> str:
    """Render one stage prompt: the image's system rules, then tagged blocks.

    Prompt text comes from the image, never from the bundle or the config file's
    location, so it cannot follow wherever AI_REVIEW_CONFIG points.
    """
    max_prompt_bytes = config.limits.max_prompt_bytes
    prompt_path = trusted_runtime_root() / "prompts" / f"{stage}.md"
    system_rules = prompt_path.read_text(encoding="utf-8")
    rendered = "\n\n".join(
        part
        for tag, body in [("SYSTEM_RULES", system_rules), *blocks]
        for part in (f"<{tag}>", body, f"</{tag}>")
    )
    if len(rendered.encode("utf-8")) > max_prompt_bytes:
        raise PromptRenderError("rendered prompt exceeds limits.max_prompt_bytes")
    return rendered


def render_review_prompt(input_dir: str | Path, config: ReviewConfig, reviewer: str) -> str:
    input_dir = Path(input_dir)
    manifest = load_json_file(input_dir / "manifest.json")
    prior_decisions_path = input_dir / "prior_decisions.json"
    prior_decisions = load_json_file(prior_decisions_path) if prior_decisions_path.exists() else {}
    diff_text = (input_dir / "mr.diff").read_text(encoding="utf-8")
    return _render(
        "review",
        config,
        [
            ("REVIEWER", reviewer),
            ("INPUT_MANIFEST_JSON", canonical_json_text(_prompt_manifest(manifest))),
            ("PRIOR_DECISIONS_JSON", canonical_json_text(prior_decisions)),
            ("RULES", _read_rules()),
            ("DIFF_STATS", _diff_stats_text(diff_text)),
            ("MR_DIFF_UNTRUSTED_DATA", diff_text),
        ],
    )


def _reviewer_aliases(reviewers: list[str]) -> dict[str, str]:
    return {
        reviewer: f"reviewer_{chr(ord('A') + index)}"
        for index, reviewer in enumerate(sorted(reviewers))
    }


def build_pooled_findings(
    manifest: dict[str, Any],
    finding_batches: list[dict[str, Any]],
    config: ReviewConfig,
    critic: str,
) -> dict[str, Any]:
    successful_batches = [
        batch for batch in finding_batches if batch.get("adapter_status") == "success"
    ]
    aliases = _reviewer_aliases([str(batch.get("reviewer", "")) for batch in successful_batches])
    blind = config.critique.blind_reviewer_identity
    findings: list[dict[str, Any]] = []
    for batch in sorted(successful_batches, key=lambda item: str(item.get("reviewer", ""))):
        reviewer = str(batch["reviewer"])
        reviewer_label = aliases[reviewer] if blind else reviewer
        for index, finding in enumerate(
            sorted(
                batch.get("findings", []),
                key=lambda item: str(item.get("source_finding_id", "")),
            ),
            start=1,
        ):
            copied = dict(finding)
            if blind and "run_local_id" in copied:
                copied["run_local_id"] = f"{reviewer_label}-{index:04d}"
            copied["reviewer"] = reviewer_label
            findings.append(copied)

    return {
        "schema_version": "pooled_findings.v1",
        "run_id": manifest["run_id"],
        "critic": critic,
        "blind_reviewer_identity": blind,
        "findings": sorted(findings, key=lambda item: str(item.get("source_finding_id", ""))),
    }


def load_successful_finding_batches(findings_dir: str | Path) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    for path in sorted(Path(findings_dir).glob("*.json")):
        batches.append(load_json_file(path))
    return batches


def render_critique_prompt(
    input_dir: str | Path,
    config: ReviewConfig,
    critic: str,
    findings_dir: str | Path,
    *,
    pooled_findings_out: str | Path | None = None,
) -> str:
    input_dir = Path(input_dir)
    manifest = load_json_file(input_dir / "manifest.json")
    pooled_findings = build_pooled_findings(
        manifest,
        load_successful_finding_batches(findings_dir),
        config,
        critic,
    )
    if pooled_findings_out is not None:
        write_canonical_json(pooled_findings_out, pooled_findings)
    return _render(
        "critique",
        config,
        [
            ("CRITIC", critic),
            ("INPUT_MANIFEST_JSON", canonical_json_text(_prompt_manifest(manifest))),
            ("RULES", _read_rules()),
            ("POOLED_FINDINGS_JSON", canonical_json_text(pooled_findings)),
        ],
    )
