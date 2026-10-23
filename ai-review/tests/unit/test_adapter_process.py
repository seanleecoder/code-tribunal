from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from ai_review.adapter_process import AdapterOutputLimitError, _run_adapter_process


@pytest.mark.parametrize("timeout", [False, True])
def test_capture_cleans_up_children_holding_output_open(tmp_path: Path, timeout: bool) -> None:
    lock_path = tmp_path / "child.lock"
    child = (
        "import fcntl, time\n"
        f"with open({str(lock_path)!r}, 'w') as lock:\n"
        "    fcntl.flock(lock, fcntl.LOCK_EX)\n"
        "    lock.write('ready')\n"
        "    lock.flush()\n"
        "    time.sleep(30)\n"
    )
    parent = (
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"lock_path = Path({str(lock_path)!r})\n"
        "while not lock_path.exists() or not lock_path.stat().st_size:\n"
        "    time.sleep(0.01)\n"
        "print('partial output', flush=True)\n"
        + ("time.sleep(30)\n" if timeout else "")
    )
    started = time.monotonic()
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            _run_adapter_process([sys.executable, "-c", parent], os.environ.copy(), 1)
        assert caught.value.output == "partial output\n"
    else:
        result = _run_adapter_process([sys.executable, "-c", parent], os.environ.copy(), 5)
        assert result.returncode == 0
        assert result.stdout == "partial output\n"
    assert time.monotonic() - started < 5
    # A killed child releases its lock even if it remains a zombie awaiting reap.
    # This proves teardown; a prompt return alone would also pass with an orphan.
    with lock_path.open() as lock:
        deadline = time.monotonic() + 1
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)


@pytest.mark.parametrize("stream", [1, 2], ids=["stdout", "stderr"])
@pytest.mark.parametrize("linger", [False, True], ids=["exited", "running"])
def test_output_limit_fails_instead_of_truncating(
    monkeypatch: pytest.MonkeyPatch, stream: int, linger: bool
) -> None:
    monkeypatch.setattr("ai_review.adapter_process._ADAPTER_OUTPUT_LIMIT_BYTES", 1024)
    script = (
        "import os, time\n"
        f"os.write({3 - stream}, b'failure detail: token=never-publish-this\\n')\n"
        f"os.write({stream}, b'x' * 1025 + b'\\nlast progress\\n')\n"
    )
    if linger:
        script += "time.sleep(30)\n"
    started = time.monotonic()
    with pytest.raises(AdapterOutputLimitError, match="exceeded 1024 bytes") as caught:
        _run_adapter_process([sys.executable, "-c", script], os.environ.copy(), 5)
    assert time.monotonic() - started < 3
    tails = [caught.value.stdout, caught.value.stderr]
    assert all(len(tail) <= 4020 for tail in tails)
    assert "last progress" in tails[stream - 1]
    assert tails[2 - stream] == "failure detail: token=[REDACTED]\n"


@pytest.mark.parametrize("unicode_prefix", [False, True])
def test_output_limit_diagnostics_redact_secrets_starting_before_the_read_window(
    monkeypatch: pytest.MonkeyPatch, unicode_prefix: bool,
) -> None:
    monkeypatch.setattr("ai_review.adapter_process._ADAPTER_OUTPUT_LIMIT_BYTES", 1024)
    stdout = (
        "-----BEGIN PRIVATE KEY-----\n"
        + "private-key-material\n" * 2000
        + "-----END PRIVATE KEY-----\nstdout failure detail\n"
    )
    stderr = "token=" + "never-publish-this" * 2000 + "\nstderr failure detail\n"
    if unicode_prefix:
        stdout = "Bearer\u2003" + "never-publish-this" * 2000 + "\nstdout failure detail\n"
        stderr = "toKen=\u2003" + "never-publish-this" * 2000 + "\nstderr failure detail\n"
    script = (
        "import os\n"
        f"os.write(1, {stdout.encode()!r})\n"
        f"os.write(2, {stderr.encode()!r})\n"
    )
    with pytest.raises(AdapterOutputLimitError) as caught:
        _run_adapter_process([sys.executable, "-c", script], os.environ.copy(), 5)
    assert caught.value.stdout == "...[truncated]...\n[REDACTED]\nstdout failure detail\n"
    assert caught.value.stderr == "...[truncated]...\n[REDACTED]\nstderr failure detail\n"


def test_stderr_is_redacted_before_trimming_and_never_streamed(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # The PEM block spans the raw tail boundary; trimming before redaction would
    # expose its final lines without the opening delimiter needed to mask them.
    diagnostic = (
        "old progress\n" * 500
        + "-----BEGIN PRIVATE KEY-----\n"
        + "private-key-material\n" * 500
        + "-----END PRIVATE KEY-----\n"
        + "token=never-publish-this\nlast progress\n"
    )
    script = (
        "import os\n"
        "os.write(1, b'{\"findings\":[]}\\n')\n"
        f"os.write(2, {diagnostic.encode()!r})\n"
    )
    result = _run_adapter_process([sys.executable, "-c", script], os.environ.copy(), 5)
    assert result.stdout == '{"findings":[]}\n'
    assert result.stderr.startswith("...[truncated]...\n")
    assert result.stderr.endswith("token=[REDACTED]\nlast progress\n")
    assert len(result.stderr) <= 4020
    assert "private-key-material" not in result.stderr
    assert "never-publish-this" not in result.stderr
    assert capfd.readouterr() == ("", "")
