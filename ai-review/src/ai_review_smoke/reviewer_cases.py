"""Reviewer-tag packaged-runtime cases.

These need the pinned CLIs, which only ``AI_REVIEW_REVIEWER_TAG`` has, so they are
a separate scope from :mod:`ai_review_smoke.base_cases` rather than skipped cases
in one suite -- a skip is indistinguishable from a check that quietly stopped
running.

The pipeline runs once in :meth:`PackagedReviewerImageTests.setUpClass` and each
case asserts one property of its artifacts. Two of the properties are additions
rather than reorganizations of the previous inline shell: the critique stage,
which no preflight exercised, and the cursor seat, which ships in the image but
was absent from the ``for reviewer in claude codex opencode`` loop.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from .manifest import PINNED_CLI_VERSION_COMMANDS
from .paths import packaged_root

# Generous relative to a mock run, which does no network I/O, but bounded so a
# hung CLI fails the preflight instead of the job's own wall clock.
_STEP_TIMEOUT_SECONDS = 300


class PackagedReviewerImageTests(unittest.TestCase):
    seats: tuple[str, ...] = ()
    review_runs: dict[str, subprocess.CompletedProcess[str]] = {}
    critique_runs: dict[str, subprocess.CompletedProcess[str]] = {}
    consensus_run: subprocess.CompletedProcess[str] | None = None
    output_dir: Path
    _workspace: Path
    _env: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        from ai_review.reviewers import REVIEWER_IDS

        root = packaged_root()
        cls.seats = tuple(sorted(REVIEWER_IDS))
        cls._workspace = Path(tempfile.mkdtemp(prefix="ai-review-packaged-smoke."))
        input_dir = cls._workspace / "inputs"
        cls.output_dir = cls._workspace / "out"
        # The shipped roster leaves cursor off, so name every seat explicitly:
        # the seat is in the image and the preflight must prove it runs there.
        # Nothing else is overridden -- critique is already on in the shipped
        # document, so this exercises the default config an install receives.
        cls._env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", tempfile.gettempdir()),
            "TMPDIR": tempfile.gettempdir(),
            # run_reviewer.sh honors PYTHON; pin it to the interpreter running this
            # suite so a seat cannot land on a different one than the runner used.
            "PYTHON": sys.executable,
            "PYTHONPATH": str(root / "src"),
            "AI_REVIEW_CONFIG": str(root / "config" / "review.yaml"),
            "AI_REVIEW_INPUT_DIR": str(input_dir),
            "AI_REVIEW_OUTPUT_DIR": str(cls.output_dir),
            "AI_REVIEW_REVIEWERS": ",".join(cls.seats),
            "AI_REVIEW_LOCAL_MOCK": "1",
            "AI_REVIEW_ALLOW_LOCAL_MOCK": "true",
        }

        prepare = cls._run(
            [
                sys.executable,
                "-m",
                "ai_review.input_bundle",
                "local",
                "--config",
                cls._env["AI_REVIEW_CONFIG"],
                "--diff",
                str(root / "tests" / "fixtures" / "diffs" / "simple.diff"),
                "--repo",
                str(root / "tests" / "fixtures" / "repos" / "simple"),
                "--out",
                str(input_dir),
            ]
        )
        if prepare.returncode != 0:
            # Every case below reads this bundle, so a failed prepare is an error
            # for the whole class rather than a misleading per-case failure.
            raise RuntimeError(f"packaged smoke prepare failed:\n{prepare.stdout}")

        runner = str(root / "adapters" / "run_reviewer.sh")
        cls.review_runs = {seat: cls._run([runner, seat, "review"]) for seat in cls.seats}
        cls.critique_runs = {seat: cls._run([runner, seat, "critique"]) for seat in cls.seats}
        cls.consensus_run = cls._run(
            [
                sys.executable,
                "-m",
                "ai_review.consensus",
                "--config",
                cls._env["AI_REVIEW_CONFIG"],
                "--inputs",
                str(input_dir),
                "--findings-dir",
                str(cls.output_dir / "findings"),
                "--critiques-dir",
                str(cls.output_dir / "critiques"),
                "--out",
                str(cls.output_dir / "consensus" / "consensus.json"),
            ]
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._workspace, ignore_errors=True)

    @classmethod
    def _run(cls, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            env=cls._env,
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            timeout=_STEP_TIMEOUT_SECONDS,
            check=False,
        )

    def _validate(self, path: Path, schema_name: str) -> dict[str, Any]:
        from ai_review.schema import load_json_file, validate_instance

        self.assertTrue(path.is_file(), f"{path} was not written")
        instance = load_json_file(path)
        validate_instance(instance, schema_name)
        self.assertIsInstance(instance, dict)
        return dict(instance)

    def test_every_adapter_script_is_executable(self) -> None:
        """A lost execute bit is invisible until a review job tries to spawn one."""
        from ai_review.reviewers import REVIEWERS, resolve_adapter_path

        scripts = [packaged_root() / "adapters" / "run_reviewer.sh"]
        scripts.extend(resolve_adapter_path(definition) for definition in REVIEWERS.values())
        for script in scripts:
            with self.subTest(script=script.name):
                self.assertTrue(script.is_file(), f"{script} is missing from the image")
                self.assertTrue(os.access(script, os.X_OK), f"{script} is not executable")

    def test_pinned_clis_report_a_version(self) -> None:
        for command in PINNED_CLI_VERSION_COMMANDS:
            with self.subTest(cli=command[0]):
                self.assertIsNotNone(
                    shutil.which(command[0]),
                    f"{command[0]} does not resolve on the image PATH",
                )
                completed = self._run(list(command))
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{' '.join(command)} failed:\n{completed.stdout}\n{completed.stderr}",
                )

    def test_local_mock_review_validates_every_seats_batch(self) -> None:
        for seat in self.seats:
            with self.subTest(reviewer=seat):
                completed = self.review_runs[seat]
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{seat} review failed:\n{completed.stdout}\n{completed.stderr}",
                )
                batch = self._validate(
                    self.output_dir / "findings" / f"{seat}.json",
                    "finding_batch.schema.json",
                )
                self.assertEqual(batch["reviewer"], seat)
                self.assertEqual(batch["adapter_status"], "success")

    def test_local_mock_critique_completes_for_every_seat(self) -> None:
        """The critique stage, which no image preflight exercised before SPEC-58."""
        for seat in self.seats:
            with self.subTest(reviewer=seat):
                completed = self.critique_runs[seat]
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{seat} critique failed:\n{completed.stdout}\n{completed.stderr}",
                )
                batch = self._validate(
                    self.output_dir / "critiques" / f"{seat}.json",
                    "critique_batch.schema.json",
                )
                # A critique batch names its author `critic`, not `reviewer`.
                self.assertEqual(batch["critic"], seat)
                self.assertEqual(batch["adapter_status"], "success")

    def test_local_consensus_validates_against_its_schema(self) -> None:
        assert self.consensus_run is not None
        self.assertEqual(
            self.consensus_run.returncode,
            0,
            f"consensus failed:\n{self.consensus_run.stdout}\n{self.consensus_run.stderr}",
        )
        consensus = self._validate(
            self.output_dir / "consensus" / "consensus.json", "consensus.schema.json"
        )
        self.assertEqual(
            sorted(consensus["successful_reviewers"]),
            list(self.seats),
            f"every seat must be usable in the packaged run: {json.dumps(consensus)[:400]}",
        )
