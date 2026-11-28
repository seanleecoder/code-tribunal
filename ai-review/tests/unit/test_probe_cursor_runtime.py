from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from ai_review import adapter_process, reviewers
from ai_review.reviewers import get_reviewer_definition

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
probe = load_repository_script("probe_cursor_runtime", ROOT / "scripts" / "probe_cursor_runtime.py")
identity_validator = load_repository_script(
    "validate_candidate_identity", ROOT / "scripts" / "validate_candidate_identity.py"
)
release_common = load_repository_script("release_common", ROOT / "scripts" / "release_common.py")
WORKFLOW = ROOT / ".github" / "workflows" / "cursor-runtime-probe.yml"

PRODUCTION = list(get_reviewer_definition("cursor").cli_arguments)
SOURCE = "a" * 40
DIGEST = f"sha256:{'c' * 64}"
IMAGE = f"ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:3.1-{SOURCE}@{DIGEST}"
IDENTITY = {"image": IMAGE, "runtime_source": SOURCE, "digest": DIGEST}


def _variant(name: str):
    return probe.VARIANTS_BY_NAME[name]


def _result(variant, **overrides: object) -> dict[str, object]:
    return {**probe.variant_result(variant, exit_code=0, filesystem="clean"), **overrides}


def _records(**overrides: dict[str, object]) -> list[dict[str, object]]:
    header = {
        "schema": probe.RESULT_STREAM_SCHEMA,
        "cli_version": "2026.11.22-e9042b2",
        "model": "composer-2.5",
    }
    return [
        header,
        *(
            {"variant": variant.name, "result": _result(variant, **overrides.get(variant.name, {}))}
            for variant in probe.VARIANTS
        ),
    ]


def _lines(records: list[dict[str, object]]) -> str:
    return "".join(json.dumps(record) + "\n" for record in records)


def _summary(out: Path) -> dict:
    return json.loads((out / "summary.json").read_text(encoding="utf-8"))


def _wrapper(
    tmp_path: Path, docker: str, identity: object = IDENTITY
) -> tuple[list[str], dict[str, str]]:
    """Argv and environment for the real wrapper with a scripted fake ``docker``."""
    fake = tmp_path / "docker"
    fake.write_text("#!/bin/sh\n" + docker, encoding="utf-8")
    fake.chmod(0o755)
    identity_file = tmp_path / "identity.json"
    if identity is not None:
        text = identity if isinstance(identity, str) else json.dumps(identity)
        identity_file.write_text(text, encoding="utf-8")
    env = {
        **os.environ,
        "CURSOR_API_KEY": "synthetic-key",
        "PATH": os.pathsep.join(
            (str(tmp_path), str(Path(sys.executable).parent), os.environ["PATH"])
        ),
        "TMPDIR": str(tmp_path),
    }
    argv = [
        "sh",
        str(ROOT / "scripts/probe_cursor_runtime.sh"),
        IMAGE,
        "composer-2.5",
        str(tmp_path / "out"),
        str(identity_file),
    ]
    return argv, env


def test_variant_flag_transforms() -> None:
    assert _variant("production").transform(PRODUCTION) == PRODUCTION

    trace = _variant("production-trace").transform(PRODUCTION)
    assert trace[trace.index("--output-format") + 1] == "stream-json"
    assert [a for a in trace if a != "stream-json"] == [a for a in PRODUCTION if a != "text"]

    # deny-policy-only is production-trace without --mode ask.
    deny_only = _variant("deny-policy-only").transform(PRODUCTION)
    assert deny_only == [a for a in trace if a not in {"--mode", "ask"}]
    assert deny_only[deny_only.index("--sandbox") + 1] == "disabled"

    sandbox = _variant("sandbox-enabled").transform(PRODUCTION)
    assert sandbox[sandbox.index("--sandbox") + 1] == "enabled"
    assert sandbox[sandbox.index("--mode") + 1] == "ask"


def test_a_missing_production_flag_stops_the_probe() -> None:
    without_mode = probe.drop_flag(PRODUCTION, "--mode")
    with pytest.raises(ValueError, match="--mode"):
        probe.drop_flag(without_mode, "--mode")


def test_hostile_prompt_exercises_absolute_file_tool_write() -> None:
    prompt = probe.hostile_prompt(Path("/probe-scratch/production/outside"))
    absolute = f"/probe-scratch/production/outside/{probe.OUTSIDE_WRITE_MARKER}"
    file_tool_steps = [line for line in prompt.splitlines() if "file editing tool" in line]
    assert any(absolute in line for line in file_tool_steps)
    assert any(probe.WRITE_MARKER in line and absolute not in line for line in file_tool_steps)


def test_evaluate_reports_every_write_and_never_passes_an_unwitnessed_run() -> None:
    before = {"README.md": "a", "src/app.py": "b"}
    read_only = probe.evaluate(before, dict(before), {}, {}, output=probe.SENTINEL)
    assert read_only["filesystem"] == "clean"
    assert read_only["verdict"] == "inconclusive"
    assert read_only["sentinel_in_stdout"] is True
    sandbox = probe.variant_result(_variant("sandbox-enabled"), exit_code=0, **read_only)
    assert sandbox["verdict"] == "inconclusive"

    written = probe.evaluate(
        before,
        {"README.md": "changed", probe.WRITE_MARKER: "c"},
        {},
        {probe.OUTSIDE_MARKER: "d"},
        output="no quote",
    )
    assert written["verdict"] == "violation"
    assert written["violations"] == [
        "changed README.md",
        "removed src/app.py",
        f"created {probe.WRITE_MARKER}",
        f"created outside {probe.OUTSIDE_MARKER}",
    ]
    assert written["sentinel_in_stdout"] is False
    assert probe.hostile_violation({"hostile": True, **written})
    assert probe.variant_result(_variant("production"))["refusal_evidence"] == "unwitnessed"
    assert probe.variant_result(_variant("sandbox-enabled"))["refusal_evidence"] == "not_applicable"


def test_tree_digest_and_redaction(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("x", encoding="utf-8")
    assert list(probe.tree_digest(tmp_path)) == [".", "a", "a/f.txt"]
    assert probe.tree_digest(tmp_path / "missing") == {}
    assert probe.redact("key=abc123", "abc123") == "key=[REDACTED]"
    assert probe.redact("text", "") == "text"


def test_tree_digest_detects_non_regular_entries_and_mode_changes(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").chmod(0o644)
    before = probe.tree_digest(tmp_path)
    (tmp_path / "new-dir").mkdir()
    (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
    os.mkfifo(tmp_path / "fifo")
    (tmp_path / "README.md").chmod(0o600)
    result = probe.evaluate(
        before,
        probe.tree_digest(tmp_path),
        {},
        {},
        output=probe.SENTINEL,
    )
    assert result["filesystem"] == "violation"
    assert sorted(result["violations"]) == [
        "changed README.md",
        "created dangling",
        "created fifo",
        "created new-dir",
    ]


def test_tree_digest_reports_replaced_ancestor_as_removed(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    watched = parent / "watched"
    watched.mkdir(parents=True)
    (watched / "README.md").write_text("x", encoding="utf-8")
    before = probe.tree_digest(watched)
    (watched / "README.md").unlink()
    watched.rmdir()
    parent.rmdir()
    parent.write_text("file", encoding="utf-8")

    after = probe.tree_digest(watched)
    assert after == {}
    result = probe.evaluate(before, after, {}, {}, output="")
    assert result["verdict"] == "violation"
    assert result["violations"] == ["removed .", "removed README.md"]


@pytest.mark.skipif(
    sys.platform not in {"linux", "android"}, reason="RLIMIT_AS is enforced on Linux kernels"
)
def test_tree_digest_hashes_large_files_in_bounded_memory(tmp_path: Path) -> None:
    size = 1 << 30
    watched = tmp_path / "watched"
    watched.mkdir()
    with (watched / "sparse").open("wb") as stream:
        stream.truncate(size)
    zeros = hashlib.sha256()
    block = bytes(1 << 20)
    for _ in range(size // len(block)):
        zeros.update(block)
    # Allow half the file's size beyond the address space already mapped (Android's
    # allocator reserves gigabytes up front): a whole-file read raises MemoryError.
    code = (
        "import pathlib, resource, runpy, sys\n"
        "probe = runpy.run_path(sys.argv[1])\n"
        "status = pathlib.Path('/proc/self/status').read_text()\n"
        "limit = int(status.split('VmSize:')[1].split()[0]) * 1024 + (1 << 29)\n"
        "resource.setrlimit(resource.RLIMIT_AS, (limit, limit))\n"
        "print(probe['tree_digest'](pathlib.Path(sys.argv[2]))['sparse'])\n"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            code,
            str(ROOT / "scripts/probe_cursor_runtime.py"),
            str(watched),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith(f":{zeros.hexdigest()}")


def test_watched_directory_entries_are_compared(tmp_path: Path) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    root.chmod(0o755)
    outside.mkdir()
    before, outside_before = probe.tree_digest(root), probe.tree_digest(outside)
    root.chmod(0o700)
    outside.rmdir()
    after = probe.tree_digest(root)
    removed = probe.evaluate(before, after, outside_before, probe.tree_digest(outside), output="")
    assert removed["violations"] == ["changed .", "removed outside ."]

    (root / "stay-private").touch()
    outside.symlink_to(root, target_is_directory=True)
    linked = probe.tree_digest(outside)
    assert list(linked) == ["."]
    replaced = probe.evaluate(before, after, outside_before, linked, output="")
    assert replaced["violations"] == ["changed .", "changed outside ."]


@pytest.mark.parametrize("timeout", [False, True])
def test_run_variant_stops_descendants_before_the_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout: bool
) -> None:
    late = tmp_path / "late-write"
    # Like adapters/cursor.sh, the launcher runs the CLI as a child, not via exec.
    child = f"(sleep 0.5; echo late > {late}) &"
    launcher = f"printf partial; {child} sleep 30" if timeout else f"{child} exit 0"
    monkeypatch.setattr(reviewers, "adapter_command", lambda _definition: ["sh", "-c", launcher])
    if timeout:
        monkeypatch.setattr(probe, "TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    result = probe.run_variant(_variant("production"), "composer-2.5", tmp_path / "scratch")
    elapsed = time.monotonic() - started

    if timeout:
        assert result["exit_code"] == 124
        assert result["stdout"] == "partial"
    else:
        assert result["exit_code"] == 0
        assert elapsed < 0.5, "a child holding stdout open delayed the result"
    # Wait past the child's write time, measured from launch.
    time.sleep(max(0.0, started + 1.5 - time.monotonic()))
    assert not late.exists(), "a launcher descendant survived the process-group kill"


def test_probe_keeps_completed_variants_after_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FlushedOutput(io.StringIO):
        flushed = 0

        def flush(self) -> None:
            self.flushed += 1
            super().flush()

    output = FlushedOutput()

    def run_variant(variant, _model, _scratch):
        assert output.flushed == list(probe.VARIANTS).index(variant) + 1
        if variant.name == "deny-policy-only":
            raise RuntimeError("boom")
        return _result(variant)

    def version(args, *, env, **_kwargs):
        assert args == ["cursor-agent", "--version"]
        assert env["TMPDIR"] == str(tmp_path)
        assert "CURSOR_API_KEY" not in env
        raise subprocess.TimeoutExpired(["cursor-agent", "--version"], 60)

    monkeypatch.setenv("CURSOR_API_KEY", "synthetic-key")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(probe, "run_variant", run_variant)
    monkeypatch.setattr(probe.subprocess, "run", version)
    assert probe.probe("composer-2.5", tmp_path / "scratch", output) == 2
    assert output.flushed == len(probe.VARIANTS) + 1
    results = tmp_path / "results.jsonl"
    results.write_text(output.getvalue(), encoding="utf-8")

    header, variants, errors = probe._read_results(results)
    assert not errors
    assert header["cli_version"] == "unavailable: TimeoutExpired"
    assert set(variants) == set(probe.VARIANTS_BY_NAME)
    failed = variants["deny-policy-only"]
    assert failed["error"] == "RuntimeError: boom"
    assert failed["verdict"] == "inconclusive" and failed["filesystem"] == "unknown"
    assert variants["production"]["error"] is None


@pytest.mark.parametrize("finish", ["exit", "timeout", "limit"])
def test_run_variant_omits_tails_clipped_before_literal_key_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, finish: str
) -> None:
    secret = "synthetic-literal-cursor-KEY-DUMMY"
    monkeypatch.setenv("CURSOR_API_KEY", secret)
    code = (
        "import os, sys, time\n"
        "sys.stderr.write(' ' + os.environ['CURSOR_API_KEY'] + ' ' + 'x' * 3968)\n"
        "sys.stderr.flush()\n"
    )
    if finish == "timeout":
        monkeypatch.setattr(probe, "TIMEOUT_SECONDS", 0.5)
        code += "time.sleep(10)\n"
    if finish == "limit":
        monkeypatch.setattr(adapter_process, "_ADAPTER_OUTPUT_LIMIT_BYTES", 1024)
        code += "sys.stdout.write('z' * 2048)\n"
    monkeypatch.setattr(reviewers, "adapter_command", lambda _: [sys.executable, "-c", code])

    record = probe.run_variant(_variant("production"), "composer-2.5", tmp_path / "scratch")
    assert "KEY-DUMMY" not in json.dumps(record)
    if finish == "limit":
        assert record["stdout"] == record["stderr"] == ""
        assert "diagnostic tails omitted" in record["error"]
    else:
        assert record["exit_code"] == (124 if finish == "timeout" else 0)
        assert record["stderr"] == probe.OMITTED_STDERR


def _collect(tmp_path: Path, *, exit_code: int = 2, secret: str = "") -> int:
    return probe.collect(
        results_path=tmp_path / "results.jsonl",
        container_stderr_path=tmp_path / "container.stderr",
        container_exit_code=exit_code,
        identity_path=tmp_path / "identity.json",
        image=IMAGE,
        out=tmp_path / "out",
        secret=secret,
    )


def test_collect_writes_redacted_artifacts_and_logs_only_status(tmp_path: Path, capsys) -> None:
    secret = 'cursor-"key"\u200b-123'
    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps(IDENTITY), encoding="utf-8")
    stderr = tmp_path / "container.stderr"
    stderr.write_text(f"leaked {secret}\n", encoding="utf-8")
    commands = "message ##[error]unexpected\n::error::unexpected"
    events = [
        {"type": "assistant", "text": "token: arbitrary", "tool_call": {"name": "write"}},
        {secret: secret, "text": "preserve\u2028line separator"},
        {secret: "collision", "[REDACTED]": "collision"},
    ]
    trace = "\r\n".join(json.dumps(event, ensure_ascii=False) for event in events)
    trace += '\nmalformed\n{"value":1e400}\n{"type":"result"}\n'
    (tmp_path / "results.jsonl").write_text(
        _lines(
            _records(
                production={"stdout": f"key {secret} token ghp_{'a' * 36}", "stderr": commands},
                **{"production-trace": {"stdout": trace}},
            )
        ),
        encoding="utf-8",
    )

    assert _collect(tmp_path, secret=secret) == 2
    assert capsys.readouterr().out == "Cursor probe status 2. Hostile violation observed False.\n"
    out = tmp_path / "out"
    expected = {"summary.json", "container.stderr"}
    expected |= {
        f"{name}.{kind}" for name in probe.VARIANTS_BY_NAME for kind in ("stdout", "stderr")
    }
    assert {path.name for path in out.iterdir()} == expected
    written = "".join(path.read_text(encoding="utf-8") for path in out.iterdir())
    assert secret not in written and f"ghp_{'a' * 36}" not in written
    published_trace = (out / "production-trace.stdout").read_text(encoding="utf-8")
    assert [json.loads(line) for line in published_trace.splitlines()] == [
        {"type": "assistant", "text": "token: [REDACTED]", "tool_call": {"name": "write"}},
        {"[REDACTED]": "[REDACTED]", "text": "preserve\u2028line separator"},
        {"type": "result"},
    ]
    summary = _summary(out)
    assert summary["status"] == 2
    assert summary["image"] == IDENTITY and summary["collect_errors"] == []
    assert summary["variants"]["production"]["stderr_tail"] == commands
    assert summary["variants"]["production-trace"]["trace_events_omitted"] == 3


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"verdict": []}, "field verdict is invalid"),
        ({"filesystem": {}}, "field filesystem is invalid"),
        ({"refusal_evidence": []}, "field refusal_evidence is invalid"),
        ({"refusal_evidence": "not_applicable"}, "refusal evidence does not match"),
        ({"violations": ["created file"]}, "inconsistent violation fields"),
    ],
)
def test_collect_rejects_invalid_records_and_preserves_prior_results(
    tmp_path: Path, bad: dict[str, object], message: str
) -> None:
    (tmp_path / "identity.json").write_text(json.dumps(IDENTITY), encoding="utf-8")
    (tmp_path / "results.jsonl").write_text(
        _lines(_records(**{"production-trace": bad})), encoding="utf-8"
    )
    assert _collect(tmp_path) == 3
    summary = _summary(tmp_path / "out")
    assert summary["status"] == 3
    assert list(summary["variants"]) == ["production"]
    assert message in summary["collect_errors"][0]


@pytest.mark.parametrize(
    "case", ["complete", "identity", "duplicate", "timeout", "unknown", "exit_mismatch"]
)
def test_collect_preserves_observed_violations(tmp_path: Path, case: str) -> None:
    identity = {} if case == "identity" else IDENTITY
    (tmp_path / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
    records = _records(
        production={"verdict": "violation", "filesystem": "violation", "violations": ["created ."]}
    )
    stream = _lines(records)
    exit_code = 1
    if case == "duplicate":
        stream = _lines([*records[:2], records[1]])
    elif case == "timeout":
        stream = _lines(records[:2]) + '{"variant":'
        exit_code = 124
    elif case == "unknown":
        stream = _lines([*records[:2], {"variant": "../escape", "result": {}}])
    elif case == "exit_mismatch":
        exit_code = 2
    (tmp_path / "results.jsonl").write_text(stream, encoding="utf-8")
    assert _collect(tmp_path, exit_code=exit_code) == (1 if case == "complete" else 3)
    summary = _summary(tmp_path / "out")
    assert summary["hostile_violation_observed"] is True
    assert summary["variants"]["production"]["verdict"] == "violation"
    if case in {"duplicate", "timeout", "unknown"}:
        assert list(summary["variants"]) == ["production"]
    assert bool(summary["collect_errors"]) == (case != "complete")
    assert (summary["image"] is None) == (case == "identity")


def test_verify_image_binds_the_source_before_the_key_is_in_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = identity_validator
    checks: list[object] = []

    def ancestor(source: str, ref: str) -> bool:
        checks.append((source, ref))
        return True

    monkeypatch.setattr(release_common, "git_is_ancestor", ancestor)
    monkeypatch.setattr(validator, "verify_pulled_image", lambda **kwargs: checks.append(kwargs))
    out = tmp_path / "identity.json"
    assert probe.verify_image(IMAGE, out) == 0
    assert checks == [
        (SOURCE, "origin/main"),
        {"role": "reviewer", "image": IMAGE, "runtime_source": SOURCE},
    ]
    assert json.loads(out.read_text(encoding="utf-8")) == IDENTITY

    monkeypatch.setattr(release_common, "git_is_ancestor", lambda *_: False)
    assert probe.verify_image(IMAGE, tmp_path / "non-ancestor.json") == 1
    assert len(checks) == 2
    assert not (tmp_path / "non-ancestor.json").exists()

    def history_error(*_args: str) -> bool:
        raise release_common.ReleaseValidationError("main ref is unavailable")

    monkeypatch.setattr(release_common, "git_is_ancestor", history_error)
    assert probe.verify_image(IMAGE, tmp_path / "missing-history.json") == 1
    assert len(checks) == 2
    assert not (tmp_path / "missing-history.json").exists()
    monkeypatch.setattr(release_common, "git_is_ancestor", ancestor)

    def reject(**_kwargs: str) -> None:
        raise validator.CandidateIdentityError("reviewer image revision label does not match")

    monkeypatch.setattr(validator, "verify_pulled_image", reject)
    for index, image in enumerate((IMAGE, IMAGE.replace(SOURCE, "main"))):
        target = tmp_path / f"refused-{index}.json"
        assert probe.verify_image(image, target) == 1
        assert not target.exists()

    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert workflow["permissions"]["attestations"] == "read"
    steps = workflow["jobs"]["probe"]["steps"]
    names = [step["name"] for step in steps]
    assert steps[names.index("Check out protected probe driver")]["with"]["fetch-depth"] == "0"
    verify = steps[names.index("Pull and verify reviewer image")]
    run = steps[names.index("Run probe")]
    assert names.index("Pull and verify reviewer image") < names.index("Run probe")
    assert 'docker pull "$REVIEWER_IMAGE"' in verify["run"]
    assert "probe_cursor_runtime.py verify-image" in verify["run"]
    assert "CURSOR_API_KEY" not in verify.get("env", {})
    assert run["env"]["CURSOR_API_KEY"] == "${{ secrets.CURSOR_API_KEY }}"
    assert "cursor-probe-identity.json" in run["run"]


_RECORDING_DOCKER = """case "$1" in
  run)
    printf '%s\\n' "$@" >"$PROBE_TEST_ARGS"
    RUN_BEHAVIOUR
    ;;
  rm)
    printf '%s %s\\n' "$2" "$3" >>"$PROBE_TEST_REMOVED"
    ;;
esac
"""


def _recording_wrapper(
    tmp_path: Path, run_behaviour: str, identity: object = IDENTITY
) -> tuple[list[str], dict[str, str], Path, Path]:
    args_file, removed = tmp_path / "args", tmp_path / "removed"
    argv, env = _wrapper(
        tmp_path, _RECORDING_DOCKER.replace("RUN_BEHAVIOUR", run_behaviour), identity
    )
    env |= {"PROBE_TEST_ARGS": str(args_file), "PROBE_TEST_REMOVED": str(removed)}
    return argv, env, args_file, removed


def _assert_container_cleaned_up(tmp_path: Path, args_file: Path, removed: Path) -> None:
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert {"--init", "--pull=never", "TMPDIR=/probe-scratch"} <= set(args)
    name = args[args.index("--name") + 1]
    assert name.startswith("cursor-probe-")
    assert set(removed.read_text(encoding="utf-8").splitlines()) == {f"-f {name}"}
    assert not list(tmp_path.glob("tmp.*"))


@pytest.mark.parametrize("docker_exit", [124, 125])
def test_wrapper_collects_completed_results_and_cleans_up_container(
    tmp_path: Path, docker_exit: int
) -> None:
    argv, env, args_file, removed = _recording_wrapper(
        tmp_path,
        'if [ "$PROBE_TEST_EXIT" = 124 ]; then cat "$PROBE_TEST_RESULTS"; fi\n'
        '    exit "$PROBE_TEST_EXIT"',
    )
    results = tmp_path / "results.jsonl"
    records = _records(production={"stdout": "safe portion before timeout"})
    results.write_text(_lines(records[:2]) + '{"variant":', encoding="utf-8")
    env |= {"PROBE_TEST_RESULTS": str(results), "PROBE_TEST_EXIT": str(docker_exit)}
    out = tmp_path / "out"
    completed = subprocess.run(
        argv, capture_output=True, text=True, env=env, check=False, timeout=30
    )

    assert completed.returncode == 3, completed.stderr
    summary = _summary(out)
    assert summary["status"] == 3
    assert summary["container_exit_code"] == docker_exit
    assert "unexpected container exit status" in summary["collect_errors"]
    if docker_exit == 124:
        assert list(summary["variants"]) == ["production"]
        assert (out / "production.stdout").read_text(
            encoding="utf-8"
        ) == "safe portion before timeout"
    else:
        assert summary["variants"] == {}
    _assert_container_cleaned_up(tmp_path, args_file, removed)


@pytest.mark.parametrize(
    ("entry", "sent", "status"),
    [("workflow", signal.SIGINT, 130), ("direct", signal.SIGTERM, 143)],
    ids=["workflow", "direct"],
)
def test_wrapper_collects_completed_results_on_cancellation(
    tmp_path: Path, entry: str, sent: signal.Signals, status: int
) -> None:
    argv, env, args_file, removed = _recording_wrapper(
        tmp_path, 'cat "$PROBE_TEST_RESULTS"\n    touch "$PROBE_TEST_READY"\n    exec sleep 30'
    )
    results, ready = tmp_path / "results.jsonl", tmp_path / "ready"
    violation = {"verdict": "violation", "filesystem": "violation", "violations": ["created ."]}
    results.write_text(_lines(_records(production=violation)[:2]), encoding="utf-8")
    env |= {"PROBE_TEST_RESULTS": str(results), "PROBE_TEST_READY": str(ready)}
    out = tmp_path / "out"
    if entry == "workflow":
        # The runner runs the step file with `bash -e {0}` and signals only that shell.
        # A file, not `bash -c`: bash would exec a lone -c command itself.
        workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["probe"]["steps"]
        step = tmp_path / "step.sh"
        run = next(s["run"] for s in steps if s["name"] == "Run probe")
        step.write_text(run + "\n", encoding="utf-8")
        (tmp_path / "identity.json").rename(tmp_path / "cursor-probe-identity.json")
        bash = shutil.which("bash")
        assert bash is not None
        argv = [bash, "-e", str(step)]
        env |= {"RUNNER_TEMP": str(tmp_path), "REVIEWER_IMAGE": IMAGE, "MODEL": "composer-2.5"}
        out = tmp_path / "cursor-probe"
    process = subprocess.Popen(
        argv,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 20
        while not ready.exists():
            assert process.poll() is None and time.monotonic() < deadline, "docker never ran"
            time.sleep(0.05)
        process.send_signal(sent)
        # Within the runner's 7.5 s SIGINT grace; sh would otherwise defer the trap
        # until the 30 s foreground command returned.
        _stdout, stderr = process.communicate(timeout=7.5)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()

    assert process.returncode == status, stderr
    summary = _summary(out)
    assert summary["status"] == 3
    assert summary["container_exit_code"] == status
    assert summary["hostile_violation_observed"] is True
    assert list(summary["variants"]) == ["production"]
    assert "unexpected container exit status" in summary["collect_errors"]
    assert (out / "production.stdout").exists()
    _assert_container_cleaned_up(tmp_path, args_file, removed)


@pytest.mark.parametrize(
    "identity",
    [None, "{", {**IDENTITY, "digest": f"sha256:{'d' * 64}"}],
    ids=["missing", "malformed", "mismatched"],
)
def test_wrapper_rejects_invalid_identity_before_running_container(
    tmp_path: Path, identity: object
) -> None:
    argv, env, args_file, _removed = _recording_wrapper(tmp_path, "exit 2", identity)
    completed = subprocess.run(
        argv, capture_output=True, text=True, env=env, check=False, timeout=30
    )

    assert completed.returncode == 3
    assert "image identity" in completed.stderr
    assert not args_file.exists(), "the key reached docker run before the identity check"
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob("tmp.*"))
