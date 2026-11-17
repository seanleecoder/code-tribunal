#!/usr/bin/env python3
"""No-shell Codex feasibility experiment under default Docker policies (COMPAT-012).

Runs the real reviewer image with no extra security options: the shell tool is
disabled and review reads only through the trusted read-only MCP server in
`codex_repo_tools_mcp.py`. Secret-free, --network none, loopback stub provider.
The console and summary carry fixed classifications only; raw captures stay in
the approved diagnostic artifact. This never changes production launches or
waives the failed default image gate. Delete with COMPAT-012.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from diagnose_codex_sandbox import observe, run_container


def case_observation(case: Path) -> dict:
    """Fixed item types/statuses and the adapter status; no tool content."""
    try:
        capture = json.loads((case / "capture.json").read_text())
        outcome_dir = (
            case / "out" / ("critiques" if (case / "out/critiques").is_dir() else "findings")
        )
        outcome = json.loads((outcome_dir / "codex.json").read_text())
    except (OSError, ValueError) as exc:
        return {"complete": False, "error": type(exc).__name__}
    items = [
        event.get("item", {})
        for event in map(json.loads, capture["stdout"].splitlines())
        if event.get("type") == "item.completed"
    ]
    return {
        "complete": True,
        "cli_exit": capture["returncode"],
        "adapter_status": outcome.get("adapter_status"),
        "item_types": sorted({str(item.get("type")) for item in items}),
        "mcp_item_statuses": sorted(
            str(item.get("status")) for item in items if item.get("type") == "mcp_tool_call"
        ),
        "mcp_item_errors": sum(
            1 for item in items if item.get("type") == "mcp_tool_call" and item.get("error")
        ),
    }


def summarize(work: Path, launch: dict) -> dict:
    try:
        suite = json.loads((work / "codex/summary.json").read_text())
    except (OSError, ValueError) as exc:
        return {"passed": False, "result": "setup_or_transport_failure", "error": str(exc)}
    observations = {
        case["case"]: case_observation(work / "codex" / case["case"]) for case in suite["cases"]
    }
    passed = (
        launch.get("exit") == 0
        and suite["passed"]
        and bool(suite["cases"])
        and all(o["complete"] for o in observations.values())
    )
    return {
        "passed": passed,
        "result": "no_shell_passed" if passed else "no_shell_failed",
        "cases": suite["cases"],
        "observations": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    identity = observe(
        ["docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", args.image]
    )
    image_id, _, platform = (identity.get("stdout") or "").strip().partition(" ")
    if identity.get("exit") != 0 or not image_id.startswith("sha256:") or platform != "linux/amd64":
        raise RuntimeError("experiment requires the already-built Linux/amd64 reviewer image")
    work = evidence / "default-policy"
    # No --security-opt, capability or sysctl change: the default launch policy.
    launch = run_container(image_id, work, [], driver_args=["--no-shell-mcp"], timeout=900)
    summary = {"image_id": image_id, "launch_options": [], **summarize(work, launch)}
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("CODEX_NO_SHELL_SUMMARY " + json.dumps(summary), flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
