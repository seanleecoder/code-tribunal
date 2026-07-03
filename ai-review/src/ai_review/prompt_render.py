from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .canonical import canonical_json_text
from .config import load_config
from .schema import load_json_file, write_canonical_json


class PromptRenderError(ValueError):
    pass


def _read_rules(rules_dir: Path) -> str:
    if not rules_dir.exists():
        return ""
    chunks: list[str] = []
    for path in sorted(rules_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(rules_dir).as_posix()
            chunks.append(f"### {rel}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(chunks)


def render_review_prompt(input_dir: str | Path, config_path: str | Path, reviewer: str) -> str:
    input_dir = Path(input_dir)
    config = load_config(config_path)
    limits: dict[str, Any] = config.get("limits", {})
    max_prompt_bytes = int(limits.get("max_prompt_bytes", 500000))

    prompt_path = input_dir / "prompts" / "review.md"
    if not prompt_path.exists():
        prompt_path = Path(config_path).resolve().parent.parent / "prompts" / "review.md"
    system_rules = prompt_path.read_text(encoding="utf-8")

    manifest = load_json_file(input_dir / "manifest.json")
    prior_decisions_path = input_dir / "prior_decisions.json"
    prior_decisions = load_json_file(prior_decisions_path) if prior_decisions_path.exists() else {}
    diff_text = (input_dir / "mr.diff").read_text(encoding="utf-8")
    rules = _read_rules(input_dir / "rules")

    rendered = "\n\n".join(
        [
            "<SYSTEM_RULES>",
            system_rules,
            "</SYSTEM_RULES>",
            "<REVIEWER>",
            reviewer,
            "</REVIEWER>",
            "<INPUT_MANIFEST_JSON>",
            canonical_json_text(manifest),
            "</INPUT_MANIFEST_JSON>",
            "<PRIOR_DECISIONS_JSON>",
            canonical_json_text(prior_decisions),
            "</PRIOR_DECISIONS_JSON>",
            "<RULES>",
            rules,
            "</RULES>",
            "<MR_DIFF_UNTRUSTED_DATA>",
            diff_text,
            "</MR_DIFF_UNTRUSTED_DATA>",
        ]
    )
    if len(rendered.encode("utf-8")) > max_prompt_bytes:
        raise PromptRenderError("rendered prompt exceeds limits.max_prompt_bytes")
    return rendered


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
    successful_batches = [
        batch for batch in finding_batches if batch.get("adapter_status") == "success"
    ]
    aliases = _reviewer_aliases([str(batch.get("reviewer", "")) for batch in successful_batches])
    blind = bool(config.get("critique", {}).get("blind_reviewer_identity", True))
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
    config_path: str | Path,
    critic: str,
    findings_dir: str | Path,
    *,
    pooled_findings_out: str | Path | None = None,
) -> str:
    input_dir = Path(input_dir)
    config = load_config(config_path)
    limits: dict[str, Any] = config.get("limits", {})
    max_prompt_bytes = int(limits.get("max_prompt_bytes", 500000))

    prompt_path = input_dir / "prompts" / "critique.md"
    if not prompt_path.exists():
        prompt_path = Path(config_path).resolve().parent.parent / "prompts" / "critique.md"
    system_rules = prompt_path.read_text(encoding="utf-8")

    manifest = load_json_file(input_dir / "manifest.json")
    rules = _read_rules(input_dir / "rules")
    pooled_findings = build_pooled_findings(
        manifest,
        load_successful_finding_batches(findings_dir),
        config,
        critic,
    )
    if pooled_findings_out is not None:
        write_canonical_json(pooled_findings_out, pooled_findings)

    rendered = "\n\n".join(
        [
            "<SYSTEM_RULES>",
            system_rules,
            "</SYSTEM_RULES>",
            "<CRITIC>",
            critic,
            "</CRITIC>",
            "<INPUT_MANIFEST_JSON>",
            canonical_json_text(manifest),
            "</INPUT_MANIFEST_JSON>",
            "<RULES>",
            rules,
            "</RULES>",
            "<POOLED_FINDINGS_JSON>",
            canonical_json_text(pooled_findings),
            "</POOLED_FINDINGS_JSON>",
        ]
    )
    if len(rendered.encode("utf-8")) > max_prompt_bytes:
        raise PromptRenderError("rendered prompt exceeds limits.max_prompt_bytes")
    return rendered


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["review", "critique"])
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--findings-dir", default="out/findings")
    parser.add_argument("--pooled-findings-out")
    args = parser.parse_args(argv)
    if args.stage == "review":
        rendered = render_review_prompt(args.input_dir, args.config, args.reviewer)
    else:
        rendered = render_critique_prompt(
            args.input_dir,
            args.config,
            args.reviewer,
            args.findings_dir,
            pooled_findings_out=args.pooled_findings_out,
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
