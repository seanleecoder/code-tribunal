from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from ai_review.config import load_config
from ai_review.input_bundle import prepare_local_bundle

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
probe = load_repository_script("canary_codex_repo_read", ROOT / "scripts/canary_codex_repo_read.py")


def _event(tool: str = "read_file", **overrides: object) -> dict:
    return {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "server": "repo",
            "status": "completed",
            "error": None,
            "tool": tool,
            "arguments": {"path": "./src/access.py"},
            "result": {"content": [{"type": "text", "text": "1\tfixture contents"}]},
            **overrides,
        },
    }


def _result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


@pytest.mark.parametrize(
    ("event", "observed"),
    [
        (_event(), True),
        (_event("grep", result=_result("src/access.py:7:fixture contents")), True),
        (_event("grep", result=_result("src/access.py.extra:7:fixture contents")), False),
        (_event("grep", result=_result("")), False),
        (_event("list_dir", result=_result("src/access.py")), False),
        (_event(status="failed", error=None), False),
        (_event(error={"message": "failed"}), False),
        (_event(server="codex"), False),
        (_event(arguments={"path": "src/gateway_access.py"}), False),
        (_event(result=_result("\n")), False),
        (
            _event(
                result=_result("[start_line 500 is past the end of the file, which has 2 lines]")
            ),
            False,
        ),
        (_event(result=_result("2\t")), False),
        (_event(result=_result("0\tcode")), False),
        (_event(result=_result("1\tdef is_allowed():")), True),
        (_event(arguments={"path": "src/../src/access.py"}), True),
        (_event(arguments={"path": "../src/access.py"}), False),
        (_event("grep", result=_result("src/../src/access.py:7:fixture contents")), True),
        (_event(type="agent_message", text=json.dumps(_event())), False),
    ],
)
def test_fixture_requires_an_actual_content_bearing_result(event: dict, observed: bool) -> None:
    assert probe.fixture_read_observed(json.dumps(event)) is observed


@pytest.mark.parametrize(
    ("runner_exit", "adapter_status"), [(0, "success"), (1, "model_error"), (0, "skipped")]
)
def test_only_admitted_review_emits_redacted_witness(
    tmp_path, monkeypatch, runner_exit, adapter_status
):
    monkeypatch.setenv("AI_REVIEW_OUTPUT_DIR", str(tmp_path))
    witness = tmp_path / probe.WITNESS_PATH
    witness.parent.mkdir()
    witness.write_text("stale witness", encoding="utf-8")
    finding_batch = tmp_path / "findings/codex.json"
    finding_batch.parent.mkdir()
    finding_batch.write_text(
        json.dumps(
            {
                "run_id": "gl-1-2",
                "effective_config_sha256": "a" * 64,
                "adapter_status": adapter_status,
                "findings": ["raw model output"],
            }
        ),
        encoding="utf-8",
    )

    def review(reviewer, stage):
        assert (reviewer, stage) == ("codex", "review")
        probe.adapter_runner._run_adapter_process()
        return runner_exit

    with (
        patch.object(probe.adapter_runner, "run_adapter", side_effect=review),
        patch.object(
            probe.adapter_runner,
            "_run_adapter_process",
            return_value=SimpleNamespace(stdout=json.dumps(_event())),
        ),
    ):
        assert probe.run("gitlab", "reviewer@sha256:example") == runner_exit

    if adapter_status == "success":
        assert json.loads(witness.read_text(encoding="utf-8")) == {
            "platform": "gitlab",
            "reviewer": "codex",
            "reviewer_image": "reviewer@sha256:example",
            "fixture": "demo-access-policy",
            "run_id": "gl-1-2",
            "effective_config_sha256": "a" * 64,
            "observed": True,
        }
    else:
        assert not witness.exists()


def test_bypassed_capture_seam_names_the_cause(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REVIEW_OUTPUT_DIR", str(tmp_path))
    finding_batch = tmp_path / "findings/codex.json"
    finding_batch.parent.mkdir()
    finding_batch.write_text(
        json.dumps(
            {"run_id": "gl-1-2", "effective_config_sha256": "a" * 64, "adapter_status": "success"}
        ),
        encoding="utf-8",
    )
    with (
        patch.object(probe.adapter_runner, "run_adapter", return_value=0),
        pytest.raises(RuntimeError, match="_run_adapter_process was not called"),
    ):
        probe.run("gitlab", "reviewer@sha256:example")
    assert not (tmp_path / probe.WITNESS_PATH).exists()


@pytest.mark.parametrize("platform", ["github", "gitlab"])
def test_generated_panel_observes_read_through_shipped_runner(tmp_path, platform):
    driver = load_repository_script(f"canary_{platform}", ROOT / f"scripts/canary_{platform}.py")
    args = argparse.Namespace(
        base_image="ghcr.io/example/ai-review-base:3.1-test@sha256:" + "a" * 64,
        reviewer_image="ghcr.io/example/ai-review-reviewer:3.1-test@sha256:" + "b" * 64,
        runtime_source="c" * 40,
        template=str(ROOT / "ai-review/ci/review.gitlab-ci.yml"),
    )
    repo = tmp_path / "demo"
    helper = repo / probe.FIXTURE_PATH
    helper.parent.mkdir(parents=True)
    helper_source = (
        "def is_allowed(username, normalized_allowed):\n"
        "    return normalize_username(username) in normalized_allowed\n"
    )
    helper.write_text(helper_source, encoding="utf-8")
    if platform == "github":
        with patch.object(driver, "push_candidate_branch") as push:
            driver.create_campaign(args)
        workflow = yaml.safe_load(push.call_args.kwargs["workflow"])
        command = next(
            step["run"]
            for step in workflow["jobs"]["review"]["steps"]
            if step.get("name") == "Run reviewer"
        )
        [caller_path] = push.call_args.kwargs["edit"](repo)
    else:
        with (
            patch.object(driver, "_raw_file", return_value=helper_source),
            patch.object(driver, "_file_exists", return_value=False),
            patch.object(driver, "push_candidate_change") as push,
        ):
            driver.create_campaign(args)
        template = yaml.safe_load(push.call_args.kwargs["template"])
        [command] = template[".review_template"]["script"]
        assert f"out/{probe.WITNESS_PATH}" in template[".review_template"]["artifacts"]["paths"]
        [caller] = push.call_args.kwargs["fixture"]
        assert caller["action"] == "create"
        caller_path = caller["file_path"]
        (repo / caller_path).write_text(caller["content"], encoding="utf-8")

    caller_lines = (repo / caller_path).read_text(encoding="utf-8").splitlines(keepends=True)
    assert helper.read_text(encoding="utf-8") == helper_source
    assert "from src.access import is_allowed, normalize_username\n" in caller_lines
    diff = tmp_path / "change.diff"
    diff.write_text(
        f"diff --git a/{caller_path} b/{caller_path}\nnew file mode 100644\n"
        f"--- /dev/null\n+++ b/{caller_path}\n@@ -0,0 +1,{len(caller_lines)} @@\n"
        + "".join(f"+{line}" for line in caller_lines),
        encoding="utf-8",
    )
    inputs = tmp_path / "inputs"
    prepare_local_bundle(load_config(ROOT / "ai-review/config/review.yaml"), diff, repo, inputs)

    # Exercise both generated shell steps and the normal adapter using a
    # captured CLI stream. Only the Codex executable is replaced locally.
    capture = json.loads(
        (ROOT / "ai-review/tests/fixtures/codex/mcp-read.json").read_text(encoding="utf-8")
    )
    stream = tmp_path / "codex.jsonl"
    stream.write_text(
        # The recorded read predates numbered read_file output; number it as the
        # shipped repository server does.
        capture["stdout"]
        .replace("sentinel.txt", str(probe.FIXTURE_PATH))
        .replace('"CAPTURED_SNAPSHOT_SENTINEL"', '"1\\tCAPTURED_SNAPSHOT_SENTINEL"'),
        encoding="utf-8",
    )
    codex = tmp_path / "codex"
    shell = shutil.which("sh")
    codex.write_text(
        f"#!{shell}\nset -eu\ntest -f src/access.py\ncat {shlex.quote(str(stream))}\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    output = tmp_path / "out"
    result = subprocess.run(
        # A bash job shell (the Actions default) that ignores SIGCHLD passes that
        # to the commands it runs; dash does not. The canary must reset it as the
        # shipped launcher does, or the runner cannot wait for Codex.
        [shutil.which("bash") or "bash", "-ec", f"trap '' CHLD\n{command}"],
        cwd=repo,
        env={
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": str(ROOT / "ai-review/src"),
            "PYTHON": sys.executable,
            "OPENROUTER_API_KEY": "fixture-token",
            "AI_REVIEW_INPUT_DIR": str(inputs),
            "AI_REVIEW_OUTPUT_DIR": str(output),
            "REVIEWER": "codex",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    witness = json.loads((output / probe.WITNESS_PATH).read_text(encoding="utf-8"))
    assert witness["observed"] is True
    assert witness["platform"] == platform
