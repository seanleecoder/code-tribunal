#!/usr/bin/env python3
"""COMPAT-013: one paid real-model check that Codex discovers its repository tools.

The image gate drives code mode with a scripted model that already knows the
tool names. Codex defers MCP tools in code mode, so a real model must find them
through ALL_TOOLS. This runs one real review with the configured default Codex
model on a tiny fixture whose planted bug lives in an unchanged file the diff
calls into, then reports fixed classifications only. Raw streams, prompts and
tool output stay in the container's private /tmp and are discarded with it.

Host:   python3 scripts/live_codex_tool_check.py <reviewer-image>
        (OPENROUTER_API_KEY in the environment; passed to Docker by name only)
Inside: python3 /probe/live_codex_tool_check.py --inside

Temporary: delete with COMPAT-013 once PR #304 records the evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

DIFF = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,4 @@
-def main():
-    return 0
+from helpers import total
+
+def main():
+    return total([10, 20, 30])
"""
HELPERS = '''def total(values):
    """Return the sum of all values."""
    result = 0
    for index in range(1, len(values)):
        result += values[index]
    return result
'''
RUNNER = r"""
import json, sys
from pathlib import Path
from ai_review import adapter_runner as runner
capture = Path(sys.argv[1])
original_run = runner._run_adapter_process
def record(*args, **kwargs):
    result = original_run(*args, **kwargs)
    capture.write_text(json.dumps({"stdout": result.stdout, "returncode": result.returncode}))
    return result
runner._run_adapter_process = record
raise SystemExit(runner.run_adapter("codex", "review"))
"""


def classify(stream: str, returncode: int, outcome: dict) -> dict:
    """Fixed fields only: no prompt, model text, tool output or finding prose."""
    items = []
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "item.completed":
            items.append(event.get("item") or {})
    calls = [item for item in items if item.get("type") == "mcp_tool_call"]
    succeeded = [c for c in calls if c.get("status") == "completed" and c.get("error") is None]
    paths = {str((c.get("arguments") or {}).get("path", "")).removeprefix("./") for c in succeeded}
    summary = {
        "cli_exit": returncode,
        "adapter_status": outcome.get("adapter_status"),
        "mcp_calls": dict(
            Counter(f"{c.get('server')}/{c.get('tool')}:{c.get('status')}" for c in calls)
        ),
        "mcp_operational_errors": sum(1 for c in calls if c.get("error") is not None),
        "command_executions": sum(1 for item in items if item.get("type") == "command_execution"),
        "read_unchanged_helpers": "helpers.py" in paths,
        "finding_count": len(outcome.get("findings", [])),
        "finding_paths": sorted(
            {str((f.get("anchor") or {}).get("new_path")) for f in outcome.get("findings", [])}
        ),
    }
    summary["passed"] = (
        summary["adapter_status"] == "success"
        and bool(succeeded)
        and all(c.get("server") == "repo" for c in calls)
        and summary["command_executions"] == 0
    )
    return summary


def inside(install: Path) -> int:
    sys.path.insert(0, str(install / "src"))
    from ai_review.config import load_config  # noqa: PLC0415
    from ai_review.input_bundle import prepare_local_bundle  # noqa: PLC0415

    with TemporaryDirectory(prefix="codex-live-", dir="/tmp") as directory:
        work = Path(directory)
        source = work / "source"
        source.mkdir()
        (source / "app.py").write_text(
            "from helpers import total\n\ndef main():\n    return total([10, 20, 30])\n"
        )
        (source / "helpers.py").write_text(HELPERS)
        (work / "change.diff").write_text(DIFF)
        config = load_config(install / "config/review.yaml")
        prepare_local_bundle(config, work / "change.diff", source, work / "inputs")
        capture = work / "capture.json"
        result = subprocess.run(
            [sys.executable, "-P", "-c", RUNNER, str(capture)],
            env={
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(install / "src"),
                "HOME": str(work / "home"),
                "OPENROUTER_API_KEY": os.environ["OPENROUTER_API_KEY"],
                "AI_REVIEW_INPUT_DIR": str(work / "inputs"),
                "AI_REVIEW_OUTPUT_DIR": str(work / "out"),
            },
            capture_output=True,
            text=True,
            timeout=840,
            check=False,
        )
        outcome = json.loads((work / "out/findings/codex.json").read_text())
        data = json.loads(capture.read_text()) if capture.is_file() else {}
        summary = {
            "model": config.reviewers["codex"].model,
            "runner_exit": result.returncode,
            **classify(data.get("stdout", ""), data.get("returncode", -1), outcome),
        }
    print("CODEX_LIVE_SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    return 0 if summary["passed"] else 1


def host_argv(image: str, script: Path, name: str) -> list[str]:
    # Default network and security options; the key crosses by name only.
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--platform",
        "linux/amd64",
        "--mount",
        f"type=bind,src={script},dst=/probe/live_codex_tool_check.py,readonly",
        "--env",
        "OPENROUTER_API_KEY",
        "--env",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        image,
        "python3",
        "/probe/live_codex_tool_check.py",
        "--inside",
    ]


def host(image: str) -> int:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY is required for the live check", file=sys.stderr)
        return 2
    name = "tribunal-codex-live-" + uuid.uuid4().hex
    try:
        result = subprocess.run(
            host_argv(image, Path(__file__).resolve(), name),
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    finally:
        subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=False)
    # Only the fixed summary line leaves the container's stdout. Without one,
    # show this script's own traceback: runner and CLI output stay captured
    # inside the container, and the key value is redacted regardless.
    lines = [line for line in result.stdout.splitlines() if line.startswith("CODEX_LIVE_SUMMARY ")]
    if lines:
        print(lines[-1])
        return result.returncode
    failure = {"passed": False, "error": "no summary", "exit": result.returncode}
    print("CODEX_LIVE_SUMMARY " + json.dumps(failure))
    print(redacted_tail(result.stderr, os.environ["OPENROUTER_API_KEY"]), file=sys.stderr)
    return 1


def redacted_tail(text: str, secret: str, *, limit: int = 3000) -> str:
    return text.replace(secret, "[REDACTED]")[-limit:] if secret else text[-limit:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?")
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--install-root", type=Path, default=Path("/opt/ai-review"))
    args = parser.parse_args()
    if args.inside:
        return inside(args.install_root)
    if not args.image:
        parser.error("image is required outside the container")
    return host(args.image)


if __name__ == "__main__":
    raise SystemExit(main())
