from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .canonical import canonical_json_text
from .config import load_config
from .schema import load_json_file


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


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["review"])
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    rendered = render_review_prompt(args.input_dir, args.config, args.reviewer)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
