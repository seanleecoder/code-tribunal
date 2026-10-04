from __future__ import annotations

from pathlib import Path
from typing import Any

from .anchors import anchor_location, finding_sort_key
from .canonical import canonical_json_text
from .config import load_config
from .consensus import validate_consensus_inputs
from .schema import load_json_file, validate_instance, write_canonical_json


class PromptRenderError(ValueError):
    pass


def _project_context(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: manifest[key]
        for key in (
            "project_path",
            "source_branch",
            "target_branch",
            "base_sha",
            "start_sha",
            "head_sha",
        )
        if key in manifest
    }


def _prior_decisions(input_dir: Path) -> dict[str, Any]:
    path = input_dir / "prior_decisions.json"
    prior = load_json_file(path) if path.exists() else {}
    # Keep the prior finding's title, category, status and path, not state hashes.
    return {
        key: [
            {
                field: item[field]
                for field in (
                    "title",
                    "category",
                    "status",
                    "path",
                )
                if field in item
            }
            for item in prior.get(key, [])
        ]
        for key in ("settled", "open")
    }


def _read_rules(rules_dir: Path) -> str:
    if not rules_dir.exists():
        return ""
    chunks: list[str] = []
    for path in sorted(rules_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(rules_dir).as_posix()
            chunks.append(f"### {rel}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(chunks)


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


def _reviewer_aliases(reviewers: list[str]) -> dict[str, str]:
    return {
        reviewer: f"reviewer_{chr(ord('A') + index)}"
        for index, reviewer in enumerate(sorted(reviewers))
    }


def build_pooled_findings(
    manifest: dict[str, Any],
    finding_batches: list[dict[str, Any]],
    config: dict[str, Any],
    critic: str,
) -> dict[str, Any]:
    validate_consensus_inputs(
        config=config, manifest=manifest, finding_batches=finding_batches, critique_batches=[]
    )
    successful_batches = [
        batch for batch in finding_batches if batch.get("adapter_status") == "success"
    ]
    aliases = _reviewer_aliases([str(batch["reviewer"]) for batch in successful_batches])
    blind = bool(config.get("critique", {}).get("blind_reviewer_identity", True))
    ordered = sorted(
        [
            {**finding, "reviewer": batch["reviewer"]}
            for batch in successful_batches
            for finding in batch["findings"]
        ],
        key=finding_sort_key,
    )
    findings, mapping = [], {}
    for index, finding in enumerate(ordered, start=1):
        short_id = f"F{index:03d}"
        mapping[short_id] = finding["source_finding_id"]
        findings.append(
            {
                "id": short_id,
                "reviewer": aliases[finding["reviewer"]] if blind else finding["reviewer"],
                "location": anchor_location(finding["anchor"]),
                **{
                    key: finding[key]
                    for key in ("severity", "category", "title", "body", "evidence", "suggestion")
                },
            }
        )
    pool = {
        "schema_version": "pooled_findings.v2",
        "run_id": manifest["run_id"],
        "critic": critic,
        "effective_config_sha256": manifest["effective_config_sha256"],
        "blind_reviewer_identity": blind,
        "source_finding_ids": mapping,
        "findings": findings,
    }
    validate_instance(pool, "pooled_findings.schema.json")
    return pool


def render_prompt(
    input_dir: str | Path,
    config_path: str | Path,
    reviewer: str,
    stage: str,
    *,
    findings_dir: str | Path | None = None,
    pooled_findings_out: str | Path | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """One context and size boundary for both model authoring stages."""
    if stage not in {"review", "critique"}:
        raise PromptRenderError("unknown model stage")
    input_dir = Path(input_dir)
    config = load_config(config_path)
    prompt_path = input_dir / "prompts" / f"{stage}.md"
    if not prompt_path.exists():
        prompt_path = Path(config_path).resolve().parent.parent / "prompts" / f"{stage}.md"
    manifest = load_json_file(input_dir / "manifest.json")
    identity_tag = "REVIEWER" if stage == "review" else "CRITIC"
    sections = [
        ("SYSTEM_RULES", prompt_path.read_text(encoding="utf-8")),
        (identity_tag, reviewer),
        ("PROJECT_CONTEXT_JSON", canonical_json_text(_project_context(manifest))),
        ("PRIOR_DECISIONS_JSON", canonical_json_text(_prior_decisions(input_dir))),
        ("RULES", _read_rules(input_dir / "rules")),
    ]
    pool = None
    if stage == "review":
        diff_text = (input_dir / "mr.diff").read_text(encoding="utf-8")
        sections.extend(
            [
                ("DIFF_STATS", _diff_stats_text(diff_text)),
                ("MR_DIFF_UNTRUSTED_DATA", diff_text),
            ]
        )
    else:
        if findings_dir is None:
            raise PromptRenderError("critique requires the finding batches")
        pool = build_pooled_findings(
            manifest,
            [load_json_file(path) for path in sorted(Path(findings_dir).glob("*.json"))],
            config,
            reviewer,
        )
        if pooled_findings_out is not None:
            write_canonical_json(pooled_findings_out, pool)
        sections.append(
            ("POOLED_FINDINGS_JSON", canonical_json_text({"findings": pool["findings"]}))
        )
    rendered = "\n\n".join(
        part for tag, value in sections for part in (f"<{tag}>", value, f"</{tag}>")
    )
    if len(rendered.encode("utf-8")) > int(
        config.get("limits", {}).get("max_prompt_bytes", 500000)
    ):
        raise PromptRenderError("rendered prompt exceeds limits.max_prompt_bytes")
    return rendered, pool
