"""Only a catalogued code-mode model may receive the Codex review prompt."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from ai_review import codex_catalog

ADAPTER = Path(__file__).resolve().parents[2] / "adapters/codex.sh"
SRC = Path(codex_catalog.__file__).resolve().parents[1]
# The shape of `codex debug models` from the pinned CLI, reduced to the fields read.
CATALOG = {
    "models": [
        {"slug": "gpt-6.1-sol", "tool_mode": "code_mode_only"},
        {"slug": "gpt-6-luna", "tool_mode": "code_mode_only"},
        {"slug": "gpt-5.5"},
        {"slug": "gpt-5.5-mini", "tool_mode": "code_mode_only"},
    ]
}


@pytest.mark.parametrize("model", ["openai/gpt-6.1-sol", "gpt-6.1-sol", "x/gpt-6-luna"])
def test_catalogued_code_mode_models_are_admitted(model: str) -> None:
    assert codex_catalog.model_error(CATALOG, model) is None


@pytest.mark.parametrize(
    "model,cause",
    [
        # Codex gives these direct tools, including tool_search.
        ("openai/gpt-5.5", "does not run in code mode"),
        # Codex would resolve this to gpt-5.5's entry, its longest catalog prefix.
        ("openai/gpt-5.5-mini", "does not run in code mode"),
        # Codex resolves these to fallback metadata, or to a base slug's entry.
        ("openai/gpt-6.1-sol:online", "not in the pinned Codex CLI's model catalog"),
        ("openai/gpt-6.1-solx", "not in the pinned Codex CLI's model catalog"),
        ("a/b/gpt-6.1-sol", "not in the pinned Codex CLI's model catalog"),
        ("OPENAI/GPT-6.1-SOL", "not in the pinned Codex CLI's model catalog"),
        ("openai/gpt-6.1", "not in the pinned Codex CLI's model catalog"),
    ],
)
def test_other_models_are_refused_with_the_cause(model: str, cause: str) -> None:
    error = codex_catalog.model_error(CATALOG, model)
    assert error is not None and cause in error and model in error


@pytest.mark.parametrize("catalog", [None, [], {"models": {}}, {"other": []}])
def test_an_unreadable_catalog_admits_nothing(catalog) -> None:
    assert codex_catalog.model_error(catalog, "openai/gpt-6.1-sol") is not None


def _fake_cli(tmp_path: Path, listing: str, status: int = 0) -> Path:
    """A codex that lists ``listing`` and records whether anything else ran."""
    cli = tmp_path / "bin/codex"
    cli.parent.mkdir()
    marker = tmp_path / "executed"
    cli.write_text(
        "#!/bin/sh\n"
        f'if [ "$*" = "debug models" ]; then printf %s {shlex.quote(listing)}; '
        f"exit {status}; fi\n"
        f"touch {shlex.quote(str(marker))}\n"
    )
    cli.chmod(0o755)
    return cli


def _run(cli: Path, model: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", "-m", "ai_review.codex_catalog", model, str(cli)],
        env={"PATH": os.environ["PATH"], "PYTHONPATH": str(SRC), "OPENROUTER_API_KEY": "secret"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_listing_runs_without_credentials(tmp_path: Path) -> None:
    cli = tmp_path / "bin/codex"
    cli.parent.mkdir()
    cli.write_text("#!/bin/sh\nenv > \"$0.env\"\nprintf '%s' '" + json.dumps(CATALOG) + "'\n")
    cli.chmod(0o755)
    assert _run(cli, "openai/gpt-6.1-sol").returncode == 0
    assert "secret" not in Path(f"{cli}.env").read_text()


@pytest.mark.parametrize(
    "listing,status,message",
    [
        ("not json", 0, "could not be read"),
        (json.dumps(CATALOG), 3, "exited 3"),
    ],
)
def test_listing_failures_refuse(tmp_path: Path, listing: str, status: int, message: str) -> None:
    result = _run(_fake_cli(tmp_path, listing, status), "openai/gpt-6.1-sol")
    assert result.returncode == 2 and not result.stdout and message in result.stderr


def test_missing_cli_refuses(tmp_path: Path) -> None:
    result = _run(tmp_path / "absent", "openai/gpt-6.1-sol")
    assert result.returncode == 2 and "could not be read" in result.stderr


def test_adapter_refuses_before_codex_receives_the_prompt(tmp_path: Path) -> None:
    _fake_cli(tmp_path, json.dumps(CATALOG))
    prompt = tmp_path / "prompt.md"
    prompt.write_text("review prompt\n")
    environment = {
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "PYTHON": sys.executable,
        "PYTHONPATH": str(SRC),
        "AI_REVIEW_REVIEWER": "codex",
        "AI_REVIEW_MODEL": "openai/gpt-5.5",
        "AI_REVIEW_RENDERED_PROMPT": str(prompt),
        "OPENROUTER_API_KEY": "secret",
    }
    result = subprocess.run(
        ["sh", str(ADAPTER), "exec"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2 and not result.stdout
    assert "does not run in code mode" in result.stderr
    assert not (tmp_path / "executed").exists()
    # The control: a code-mode model reaches the CLI.
    environment["AI_REVIEW_MODEL"] = "openai/gpt-6.1-sol"
    subprocess.run(["sh", str(ADAPTER), "exec"], env=environment, timeout=30, check=False)
    assert (tmp_path / "executed").exists()
