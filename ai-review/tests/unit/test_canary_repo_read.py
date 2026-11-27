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
from ai_review import adapter_runner
from ai_review import canary_repo_read as probe
from ai_review.config import load_config
from ai_review.input_bundle import prepare_local_bundle
from ai_review.reviewers import CLAUDE_REPOSITORY_TOOLS, CODEX_REPO_SERVER

from tests.support.adapter_inputs import bind_manifest_to_config, write_adapter_input_bundle
from tests.support.canary_fixture import DEMO_ACCESS_SOURCE
from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "ai-review/config/review.yaml"
CAPTURE_PATH = ROOT / "ai-review/tests/fixtures/codex/mcp-read.json"


def _result(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _event(tool: str = "read_file", **overrides: object) -> dict:
    return {
        "type": "item.completed",
        "item": {
            "type": "mcp_tool_call",
            "server": CODEX_REPO_SERVER,
            "status": "completed",
            "error": None,
            "tool": tool,
            "arguments": {"path": "./src/access.py"},
            "result": _result("1\tfixture contents"),
            **overrides,
        },
    }


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
    assert probe.codex_fixture_read_observed(json.dumps(event)) is observed


@pytest.fixture
def review_output(tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    write_adapter_input_bundle(inputs)
    bind_manifest_to_config(inputs, CONFIG_PATH)
    output = tmp_path / "out"
    monkeypatch.setenv("AI_REVIEW_INPUT_DIR", str(inputs))
    monkeypatch.setenv("AI_REVIEW_OUTPUT_DIR", str(output))
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-token")
    monkeypatch.delenv("AI_REVIEW_LOCAL_MOCK", raising=False)
    for name in probe.CANARY_REVIEW_ENV:
        monkeypatch.delenv(name, raising=False)
    return output


@pytest.mark.parametrize(
    "settings",
    [
        {probe.PLATFORM_ENV: "github"},
        {probe.IMAGE_ENV: "candidate"},
        {probe.PLATFORM_ENV: "other", probe.IMAGE_ENV: "candidate"},
        {probe.PLATFORM_ENV: "gitlab", probe.IMAGE_ENV: " "},
        {probe.PLATFORM_ENV: "gitlab", probe.IMAGE_ENV: "candidate", "AI_REVIEW_LOCAL_MOCK": "1"},
    ],
)
def test_bad_canary_controls_clear_stale_evidence(review_output, monkeypatch, settings):
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    witness = review_output / probe.witness_path("codex")
    witness.parent.mkdir(parents=True)
    witness.write_text("stale witness", encoding="utf-8")
    with (
        patch.object(adapter_runner, "render_review_prompt") as render,
        patch.object(adapter_runner, "_run_adapter_process") as child,
    ):
        assert adapter_runner.run_adapter("codex", "review") == 1
    render.assert_not_called()
    child.assert_not_called()
    outcome = json.loads((review_output / "findings/codex.json").read_text(encoding="utf-8"))
    assert outcome["adapter_status"] == "config_error"
    assert outcome["error_class"] == "ConfigError"
    assert not witness.exists()


def _canary(monkeypatch, platform: str = "gitlab") -> None:
    monkeypatch.setenv(probe.PLATFORM_ENV, platform)
    monkeypatch.setenv(probe.IMAGE_ENV, "candidate")


@pytest.mark.parametrize("canary", [False, True])
def test_failed_review_writes_no_canary_evidence(review_output, monkeypatch, canary):
    witness = review_output / probe.witness_path("codex")
    if canary:
        _canary(monkeypatch, "github")
        witness.parent.mkdir(parents=True)
        witness.write_text("stale witness", encoding="utf-8")

    def fail(_command, env, *_args, **_kwargs):
        assert not probe.CANARY_REVIEW_ENV.intersection(env)
        prompt = Path(env["AI_REVIEW_RENDERED_PROMPT"]).read_text(encoding="utf-8")
        assert ("<CANARY_REVIEW>" in prompt) is canary
        return SimpleNamespace(returncode=1, stdout="", stderr="failed", stderr_matched=False)

    with patch.object(adapter_runner, "_run_adapter_process", side_effect=fail) as child:
        assert adapter_runner.run_adapter("codex", "review") == 1
    child.assert_called_once()
    assert not witness.exists()


@pytest.mark.parametrize(
    ("platform", "observed"), [("github", True), ("gitlab", True), ("gitlab", False)]
)
def test_generated_panel_observes_read_through_shipped_runner(tmp_path, platform, observed):
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
    helper.write_text(DEMO_ACCESS_SOURCE, encoding="utf-8")
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
            patch.object(driver, "_raw_file", return_value=DEMO_ACCESS_SOURCE),
            patch.object(driver, "_file_exists", return_value=False),
            patch.object(driver, "push_candidate_change") as push,
        ):
            driver.create_campaign(args)
        template = yaml.safe_load(push.call_args.kwargs["template"])
        [command] = template[".review_template"]["script"]
        assert template[".review_template"]["image"] == args.reviewer_image
        assert template[".critique_template"]["image"] == "$AI_REVIEW_REVIEWER_IMAGE"
        assert f"out/{probe.WITNESS_DIR}/" in template[".review_template"]["artifacts"]["paths"]
        [caller] = push.call_args.kwargs["fixture"]
        assert caller["action"] == "create"
        caller_path = caller["file_path"]
        (repo / caller_path).write_text(caller["content"], encoding="utf-8")

    caller_lines = (repo / caller_path).read_text(encoding="utf-8").splitlines(keepends=True)
    assert helper.read_text(encoding="utf-8") == DEMO_ACCESS_SOURCE
    assert "from src.access import is_allowed\n" in caller_lines
    assert "    return is_allowed(username, allowed_user)\n" in caller_lines
    diff = tmp_path / "change.diff"
    diff.write_text(
        f"diff --git a/{caller_path} b/{caller_path}\nnew file mode 100644\n"
        f"--- /dev/null\n+++ b/{caller_path}\n@@ -0,0 +1,{len(caller_lines)} @@\n"
        + "".join(f"+{line}" for line in caller_lines),
        encoding="utf-8",
    )
    inputs = tmp_path / "inputs"
    prepare_local_bundle(load_config(CONFIG_PATH), diff, repo, inputs)

    # Exercise both generated shell steps and the normal adapter using a
    # captured CLI stream. Only the Codex executable is replaced locally.
    captured_stdout = json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))["stdout"]
    if not observed:
        captured_stdout = (
            "\n".join(
                line for line in captured_stdout.splitlines() if '"mcp_tool_call"' not in line
            )
            + "\n"
        )
    stream = tmp_path / "codex.jsonl"
    stream.write_text(
        # The recorded read predates numbered read_file output; number it as the
        # shipped repository server does.
        captured_stdout.replace("sentinel.txt", str(probe.FIXTURE_PATH)).replace(
            '"CAPTURED_SNAPSHOT_SENTINEL"', '"1\\tCAPTURED_SNAPSHOT_SENTINEL"'
        ),
        encoding="utf-8",
    )
    codex = tmp_path / "codex"
    shell = shutil.which("sh")
    codex.write_text(
        f"#!{shell}\nset -eu\ntest -f src/access.py\n"
        + "".join(f'test -z "${{{name}+set}}"\n' for name in probe.CANARY_REVIEW_ENV)
        + "grep -Fq 'Read src/access.py with the available repository tools'\n"
        + f"cat {shlex.quote(str(stream))}\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    output = tmp_path / "out"
    launcher = "/opt/ai-review/adapters/run_reviewer.sh"
    assert launcher in command
    command = command.replace(
        launcher, shlex.quote(str(ROOT / "ai-review/adapters/run_reviewer.sh"))
    )
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
    witness = json.loads((output / probe.witness_path("codex")).read_text(encoding="utf-8"))
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    assert witness == {
        "platform": platform,
        "reviewer": "codex",
        "reviewer_image": args.reviewer_image,
        "fixture": "demo-access-policy",
        "run_id": manifest["run_id"],
        "effective_config_sha256": manifest["effective_config_sha256"],
        "observed": observed,
    }


ROOT_DIR = "/w/out/.tmp/claude-review-root.7"


def _claude_stream(name: str, arguments: dict, text: str, *, is_error: bool = False) -> str:
    result: dict = {"type": "tool_result", "tool_use_id": "toolu_1", "content": text}
    if is_error:
        result["is_error"] = True
    call = {"type": "tool_use", "id": "toolu_1", "name": name, "input": arguments}
    return "\n".join(
        json.dumps(event)
        for event in (
            {"type": "assistant", "message": {"content": [call]}},
            {"type": "user", "message": {"content": [result]}},
        )
    )


READ = {"file_path": f"{ROOT_DIR}/src/access.py"}


@pytest.mark.parametrize(
    ("stdout", "observed"),
    [
        (_claude_stream("Read", READ, "1\tdef is_allowed(user, allowed):"), True),
        (_claude_stream("Read", READ, "     7\t    return user == allowed"), True),
        (_claude_stream("Read", READ, "1\tdef is_allowed():", is_error=True), False),
        (_claude_stream("Read", READ, "File does not exist."), False),
        (_claude_stream("Read", READ, "1\t"), False),
        (_claude_stream("Read", {"file_path": f"{ROOT_DIR}/src/other.py"}, "1\tx = 1"), False),
        # An absolute path counts only beneath the reviewed snapshot.
        (_claude_stream("Read", {"file_path": "/elsewhere/src/access.py"}, "1\tx = 1"), False),
        (_claude_stream("Grep", {"pattern": "def"}, "src/access.py:1:def is_allowed():"), True),
        (
            _claude_stream("Grep", {"pattern": "def"}, f"{ROOT_DIR}/src/access.py:3:def f():"),
            True,
        ),
        (_claude_stream("Grep", {"pattern": "def"}, "No matches found"), False),
        (_claude_stream("Glob", {"pattern": "**"}, "src/access.py"), False),
        # The model's own text naming the helper is not a read.
        (
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "src/access.py:1:def"}]},
                }
            ),
            False,
        ),
    ],
)
def test_claude_witness_requires_a_successful_correlated_result(stdout: str, observed: bool):
    assert probe.claude_fixture_read_observed(stdout, ROOT_DIR) is observed


GREP_TIMEOUT = (
    "The search stopped before it finished, most likely at its time limit, so these "
    "results are incomplete: more matches may exist. Search a more specific path or pattern."
)


def _file_grep(path: str = f"{ROOT_DIR}/src/access.py", **options) -> dict:
    return {"pattern": "def", "path": path, "output_mode": "content", **options}


@pytest.mark.parametrize(
    ("arguments", "text", "observed"),
    [
        # Claude Code 2.1.332 omits the filename when Grep searches one file.
        (_file_grep(), "1:def is_allowed(user, allowed):", True),
        (_file_grep("src/access.py"), "7:    return user == allowed", True),
        (_file_grep("src/../src/access.py", **{"-n": True}), "1:def is_allowed():", True),
        (_file_grep(**{"-n": False}), "def is_allowed(user, allowed):", True),
        (_file_grep(), "No matches found", False),
        (_file_grep(**{"-n": False}), "No matches found", False),
        (_file_grep(**{"-n": False}), f"{GREP_TIMEOUT}\n\nNo matches found", False),
        (
            _file_grep(**{"-n": False}),
            "No entries at this offset\n\n[Showing results with pagination = offset: 5]",
            False,
        ),
        (_file_grep(), "", False),
        (_file_grep(), "1:", False),
        (_file_grep(**{"-n": False}), "\n  \n", False),
        (_file_grep(**{"-C": 1}), "3-def is_allowed():", False),
        (_file_grep(**{"-n": 1}), "1:def is_allowed():", False),
        (_file_grep(output_mode="files_with_matches"), "src/access.py", False),
        ({"pattern": "def", "path": "src/access.py"}, "src/access.py", False),
        (_file_grep(output_mode="count"), "src/access.py:2", False),
        (_file_grep("src/other.py"), "1:def is_allowed():", False),
        (_file_grep("/elsewhere/src/access.py"), "1:def is_allowed():", False),
    ],
)
def test_claude_witness_accepts_single_file_content_grep(
    arguments: dict, text: str, observed: bool
) -> None:
    stdout = _claude_stream("Grep", arguments, text)
    assert probe.claude_fixture_read_observed(stdout, ROOT_DIR) is observed
    errored = _claude_stream("Grep", arguments, text, is_error=True)
    assert probe.claude_fixture_read_observed(errored, ROOT_DIR) is False


def test_claude_witness_ignores_a_result_without_its_call():
    stdout = _claude_stream("Read", READ, "1\tdef is_allowed():").split("\n")[1]
    assert probe.claude_fixture_read_observed(stdout, ROOT_DIR) is False


def _part(name: str, text: str, *, status: str = "completed", **inputs: str) -> dict:
    state = {"status": status, "input": inputs, "content": [{"type": "text", "text": text}]}
    return {"type": "tool", "id": "call_1", "name": name, "state": state}


OPENCODE_ROOT = "/w/out/.tmp/opencode-home/opencode-review-root.9"


@pytest.mark.parametrize(
    ("part", "observed"),
    [
        (
            _part(
                "read",
                "Read file src/access.py, lines 1-2\n1: def is_allowed():",
                path="src/access.py",
            ),
            True,
        ),
        (_part("read", "1: def is_allowed():", path=f"{OPENCODE_ROOT}/src/access.py"), True),
        (_part("read", "1: def is_allowed():", path="src/other.py"), False),
        (_part("read", "Read file src/access.py, lines 1-0", path="src/access.py"), False),
        (_part("read", "1: def is_allowed():", status="error", path="src/access.py"), False),
        (
            _part(
                "grep",
                f"Found 1 matches\n{OPENCODE_ROOT}/src/access.py:\n  Line 3: return x\n",
                pattern="x",
            ),
            True,
        ),
        (
            _part(
                "grep",
                f"Found 1 matches\n{OPENCODE_ROOT}/src/other.py:\n  Line 3: return x\n",
                pattern="x",
            ),
            False,
        ),
        (_part("grep", "No matches found", pattern="x"), False),
        (_part("glob", f"{OPENCODE_ROOT}/src/access.py", pattern="**"), False),
    ],
)
def test_opencode_witness_requires_a_completed_content_bearing_part(part: dict, observed: bool):
    assert probe.opencode_fixture_read_observed([part], OPENCODE_ROOT) is observed


def test_a_claude_canary_review_writes_its_own_bound_witness(review_output, monkeypatch):
    _canary(monkeypatch)
    stale = review_output / probe.witness_path("claude")
    stale.parent.mkdir(parents=True)
    stale.write_text("stale witness", encoding="utf-8")

    def run(_command, env, *_args, cwd, **_kwargs):
        assert not probe.CANARY_REVIEW_ENV.intersection(env)
        assert probe.OPENCODE_OBSERVATION_ENV not in env
        assert not stale.exists()
        read = {"file_path": f"{cwd}/src/access.py"}
        init = {"type": "system", "subtype": "init", "tools": sorted(CLAUDE_REPOSITORY_TOOLS)}
        stream = (
            json.dumps(init)
            + "\n"
            + _claude_stream("Read", read, "1\tdef is_allowed():")
            + "\n"
            + json.dumps(
                {"type": "result", "subtype": "success", "structured_output": {"findings": []}}
            )
        )
        return SimpleNamespace(returncode=0, stdout=stream, stderr="", stderr_matched=False)

    with patch.object(adapter_runner, "_run_adapter_process", side_effect=run):
        assert adapter_runner.run_adapter("claude", "review") == 0
    witness = json.loads(stale.read_text(encoding="utf-8"))
    assert witness["reviewer"] == "claude" and witness["observed"] is True
    assert not (review_output / probe.witness_path("codex")).exists()


@pytest.mark.parametrize("reported", ["true", "false", ""])
def test_an_opencode_canary_review_reads_only_its_private_observation(
    review_output, monkeypatch, reported
):
    _canary(monkeypatch)
    seen = {}

    def run(_command, env, *_args, **_kwargs):
        assert not probe.CANARY_REVIEW_ENV.intersection(env)
        observation = Path(env[probe.OPENCODE_OBSERVATION_ENV])
        # Outside the reviewed snapshot and the uploaded artifacts.
        assert review_output not in observation.parents
        assert observation.read_text(encoding="utf-8") == ""
        seen["path"] = observation
        observation.write_text(reported, encoding="utf-8")
        return SimpleNamespace(
            returncode=0, stdout='{"findings":[]}', stderr="", stderr_matched=False
        )

    with patch.object(adapter_runner, "_run_adapter_process", side_effect=run):
        assert adapter_runner.run_adapter("opencode", "review") == 0
    witness = json.loads((review_output / probe.witness_path("opencode")).read_text())
    assert witness["reviewer"] == "opencode"
    assert witness["observed"] is (reported == "true")
    assert not seen["path"].exists()


def test_a_failed_opencode_canary_review_removes_its_observation(review_output, monkeypatch):
    _canary(monkeypatch)
    seen = {}

    def run(_command, env, *_args, **_kwargs):
        seen["path"] = Path(env[probe.OPENCODE_OBSERVATION_ENV])
        seen["path"].write_text("true", encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="", stderr="failed", stderr_matched=False)

    with patch.object(adapter_runner, "_run_adapter_process", side_effect=run):
        assert adapter_runner.run_adapter("opencode", "review") == 1
    assert not (review_output / probe.witness_path("opencode")).exists()
    assert not seen["path"].exists()


@pytest.mark.parametrize("reviewer", ["claude", "codex", "opencode", "cursor"])
def test_the_generated_step_gives_canary_controls_only_to_witnessed_seats(tmp_path, reviewer):
    common = load_repository_script(
        "candidate_canary_common", ROOT / "scripts/candidate_canary_common.py"
    )
    launcher = tmp_path / "run_reviewer.sh"
    launcher.write_text(
        "#!/bin/sh\n"
        + "".join(f'echo "{name}=${{{name}-unset}}"\n' for name in sorted(probe.CANARY_REVIEW_ENV))
    )
    launcher.chmod(0o755)
    command = common.panel_review_command("github", "image@sha256:x").replace(
        "/opt/ai-review/adapters/run_reviewer.sh", str(launcher)
    )
    result = subprocess.run(
        ["sh", "-ec", command],
        env={"PATH": os.environ["PATH"], "REVIEWER": reviewer},
        capture_output=True,
        text=True,
        check=True,
    )
    witnessed = reviewer in probe.WITNESSED_REVIEWERS
    assert (f"{probe.PLATFORM_ENV}=github" in result.stdout) is witnessed
    assert (f"{probe.PLATFORM_ENV}=unset" in result.stdout) is not witnessed
