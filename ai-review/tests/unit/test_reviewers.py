from __future__ import annotations

import copy
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest import mock

import yaml
from ai_review.adapter_process import (
    _ANTHROPIC_OPENROUTER_BASE_URL,
    _OPENROUTER_BASE_URL,
    _build_adapter_env,
)
from ai_review.adapter_runner import _EXIT_ERROR, run_adapter
from ai_review.config import ConfigError, validate_config
from ai_review.reviewers import (
    REVIEWER_IDS,
    REVIEWERS,
    ReviewerRegistryError,
    resolve_adapter_path,
    trusted_runtime_root,
)
from ai_review.schema import load_json_file

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.adapter_inputs import write_adapter_input_bundle  # noqa: E402

_AI_REVIEW_ROOT = Path(__file__).resolve().parents[2]
_SHIPPED_CONFIG = _AI_REVIEW_ROOT / "config" / "review.yaml"
_GITHUB_WORKFLOW = _AI_REVIEW_ROOT / "ci" / "review.github-actions.yml"
_GITLAB_TEMPLATE = _AI_REVIEW_ROOT / "ci" / "review.gitlab-ci.yml"


class ReviewerRegistryTests(unittest.TestCase):
    def test_registry_is_exactly_the_supported_four_seats(self) -> None:
        self.assertEqual(REVIEWER_IDS, {"claude", "codex", "opencode", "cursor"})
        self.assertEqual(
            {definition.reviewer_id for definition in REVIEWERS.values()},
            REVIEWER_IDS,
        )

    def test_registry_adapters_are_contained_existing_executables(self) -> None:
        self.assertEqual(trusted_runtime_root(), _AI_REVIEW_ROOT)
        for definition in REVIEWERS.values():
            with self.subTest(reviewer=definition.reviewer_id):
                self.assertFalse(Path(definition.adapter_path).is_absolute())
                resolved = resolve_adapter_path(definition)
                self.assertTrue(resolved.is_relative_to(trusted_runtime_root()))
                self.assertTrue(resolved.is_file())
                self.assertTrue(resolved.stat().st_mode & stat.S_IXUSR)

    def test_absolute_and_escaping_registry_paths_are_rejected(self) -> None:
        definition = REVIEWERS["codex"]
        for adapter_path in ("/tmp/adapter.sh", "../adapter.sh"):
            with self.subTest(adapter_path=adapter_path), self.assertRaises(
                ReviewerRegistryError
            ):
                resolve_adapter_path(replace(definition, adapter_path=adapter_path))

    def test_all_seats_support_both_stages_and_only_cursor_rejects_effort(self) -> None:
        for reviewer_id, definition in REVIEWERS.items():
            with self.subTest(reviewer=reviewer_id):
                self.assertEqual(definition.supported_stages, {"review", "critique"})
                self.assertEqual(definition.supports_effort, reviewer_id != "cursor")

    def test_adapter_environment_copies_exact_registry_credentials(self) -> None:
        all_credentials = {"OPENROUTER_API_KEY", "CURSOR_API_KEY", "ANTHROPIC_API_KEY"}
        source = {name: f"secret-for-{name}" for name in all_credentials}
        source.update(
            {
                "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
                "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            }
        )
        with mock.patch.dict(os.environ, source, clear=True):
            for reviewer_id, definition in REVIEWERS.items():
                with self.subTest(reviewer=reviewer_id):
                    env = _build_adapter_env(
                        reviewer=reviewer_id,
                        stage="review",
                        model="model",
                        input_dir=Path("inputs"),
                        output_dir=Path("out"),
                        reviewer_config={"timeout_seconds": 30},
                        reviewer_definition=definition,
                        prompt_tmp=None,
                    )
                    copied = set(env) & all_credentials
                    self.assertEqual(copied, set(definition.credential_variables))

    def test_each_adapter_reads_exactly_its_registry_require_real_control(self) -> None:
        """The registry owns the control name, so the adapter must read that one.

        `_build_adapter_env` forwards only `require_real_control`. A second name
        read as a fallback is either dead (it never arrives) or a fail-closed
        guard the runner silently stops delivering, so neither is acceptable.
        """
        all_controls = {
            definition.require_real_control for definition in REVIEWERS.values()
        }
        for reviewer_id, definition in REVIEWERS.items():
            with self.subTest(reviewer=reviewer_id):
                adapter = resolve_adapter_path(definition).read_text(encoding="utf-8")
                assignment = next(
                    line
                    for line in adapter.splitlines()
                    if line.startswith("REQUIRE_REAL=")
                )
                self.assertIn(definition.require_real_control, assignment)
                for other in all_controls - {definition.require_real_control}:
                    self.assertNotIn(other, adapter)

    def test_endpoint_is_injected_from_the_registry_endpoint_kind(self) -> None:
        """The endpoint is supplied by the runner, never read from the caller.

        Each endpoint_kind has exactly one accepted host, so no caller — the
        reviewer-image preflight, `make review-local`, a consumer pipeline — has
        to know or export the URL, and an ambient value cannot redirect egress.
        """
        endpoint_names = {"ANTHROPIC_BASE_URL", "OPENROUTER_BASE_URL"}
        expected_by_kind = {
            "anthropic_openrouter": {"ANTHROPIC_BASE_URL": _ANTHROPIC_OPENROUTER_BASE_URL},
            "openrouter": {"OPENROUTER_BASE_URL": _OPENROUTER_BASE_URL},
            "cursor_backend": {},
        }
        self.assertEqual(
            {definition.endpoint_kind for definition in REVIEWERS.values()},
            set(expected_by_kind),
            "a new endpoint_kind must state the endpoints it injects",
        )
        hostile = dict.fromkeys(endpoint_names, "https://openrouter.ai.evil.com/api")
        for ambient in ({}, hostile):
            with mock.patch.dict(os.environ, ambient, clear=True):
                for reviewer_id, definition in REVIEWERS.items():
                    with self.subTest(reviewer=reviewer_id, ambient=bool(ambient)):
                        env = _build_adapter_env(
                            reviewer=reviewer_id,
                            stage="review",
                            model="model",
                            input_dir=Path("inputs"),
                            output_dir=Path("out"),
                            reviewer_config={"timeout_seconds": 30},
                            reviewer_definition=definition,
                            prompt_tmp=None,
                        )
                        self.assertEqual(
                            {k: v for k, v in env.items() if k in endpoint_names},
                            expected_by_kind[definition.endpoint_kind],
                        )


class ReviewerAdapterContractTests(unittest.TestCase):
    """The shared runner's behavior, with the axis chosen per property.

    `adapter_runner.py` implements one run loop for every seat and both stages, so
    the axis a case is parameterized over is a claim about where the behavior comes
    from, not a default. Parameterizing shared-runner behavior over seats and stages
    multiplies runtime without adding signal, so each case below states which axis is
    load-bearing and why.

    Cases that are neither per seat nor per stage live in `test_adapter_runner.py`,
    run once against the shared runner: the local-mock authorization gates, model-ID
    validation before spawn, timeout process-group termination, and the malformed and
    valid output paths. Behavior that genuinely differs between the CLIs -- Claude's
    Anthropic-compatible OpenRouter routing, Codex config/effort flags, OpenCode's
    server and trusted ripgrep, Cursor's ask mode and disposable HOME -- lives in
    `test_openrouter_adapters.py`.
    """

    def _run_seat(
        self,
        reviewer: str,
        stage: str,
        *,
        env: dict[str, str],
        bundle: bool,
        expected_exit: int = 0,
    ) -> tuple[dict[str, object] | None, dict[str, object]]:
        """Run one seat and stage in a sandbox, returning its batch and status.

        The batch is ``None`` when the runner deliberately wrote none: a failure it
        cannot stamp an effective-config digest onto emits a status without a batch,
        so consensus does not consume a placeholder.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "inputs"
            output_dir = root / "out"
            if bundle:
                write_adapter_input_bundle(input_dir)
            else:
                input_dir.mkdir(parents=True)
            sandbox = {
                "AI_REVIEW_INPUT_DIR": str(input_dir),
                "AI_REVIEW_OUTPUT_DIR": str(output_dir),
                "AI_REVIEW_CONFIG": str(_SHIPPED_CONFIG),
                **env,
            }
            with mock.patch.dict(os.environ, sandbox, clear=True):
                # PATH is deliberately absent from `sandbox`: a seat that reached a
                # real CLI would find none, so nothing here can pass by accident on
                # a developer machine that happens to have one installed.
                self.assertEqual(run_adapter(reviewer, stage), expected_exit)
            stage_dir = "findings" if stage == "review" else "critiques"
            status_stem = reviewer if stage == "review" else f"critique-{reviewer}"
            batch_path = output_dir / stage_dir / f"{reviewer}.json"
            return (
                load_json_file(batch_path) if batch_path.exists() else None,
                load_json_file(output_dir / "status" / f"{status_stem}.json"),
            )

    def test_every_seat_reaches_the_mock_from_a_clean_shell(self) -> None:
        """Per seat, not per stage: the endpoint and credential are seat fields.

        Nothing about reaching the mock depends on the stage -- the stage only
        reaches `adapter_process.py` as AI_REVIEW_STAGE and the timeout budget -- but
        it does depend on the seat's registry entry. A seat that required a
        caller-supplied endpoint or credential would fail here while a
        representative-seat case stayed green, which is exactly how a claude-only
        endpoint requirement once shipped: the reviewer-image preflight and
        `make review-local` both run from a clean shell with no endpoint exported.

        The roster is authored per seat because the shipped default leaves cursor
        off, and `config.py` enforces a floor of three enabled seats.
        """
        for reviewer in sorted(REVIEWER_IDS):
            with self.subTest(reviewer=reviewer):
                batch, status = self._run_seat(
                    reviewer,
                    "review",
                    env={
                        "AI_REVIEW_REVIEWERS": _roster_enabling(reviewer),
                        "AI_REVIEW_LOCAL_MOCK": "1",
                        "AI_REVIEW_ALLOW_LOCAL_MOCK": "true",
                        "PYTHON": sys.executable,
                        "PYTHONPATH": str(_AI_REVIEW_ROOT / "src"),
                    },
                    bundle=True,
                )

                self.assertEqual(batch["adapter_status"], "success")
                self.assertEqual(batch["reviewer"], reviewer)
                self.assertEqual(status["status"], "success")

    def test_disabled_seat_writes_a_skipped_artifact_without_spawning_a_cli(self) -> None:
        """Per seat AND per stage: both axes are load-bearing here.

        `enabled` is a per-seat key, and `_output_file(stage, reviewer)` puts the
        skipped artifact on a stage-dependent path with a stage-dependent schema, so
        neither axis can be collapsed without losing a real path. Eight cases is the
        smallest set that covers them.

        The roster names the other three seats, which both disables the seat under
        test and satisfies the three-enabled-seat floor `config.py` enforces.
        """
        for reviewer in sorted(REVIEWER_IDS):
            for stage, schema_version in (
                ("review", "finding_batch.v1"),
                ("critique", "critique_batch.v1"),
            ):
                with self.subTest(reviewer=reviewer, stage=stage), mock.patch(
                    "ai_review.adapter_runner._run_adapter_process",
                    side_effect=AssertionError("a disabled seat must not spawn an adapter"),
                ):
                    batch, status = self._run_seat(
                        reviewer,
                        stage,
                        env={"AI_REVIEW_REVIEWERS": _roster_disabling(reviewer)},
                        bundle=False,
                    )

                self.assertEqual(batch["adapter_status"], "skipped")
                self.assertEqual(batch["schema_version"], schema_version)
                self.assertEqual(status["status"], "skipped")
                self.assertIs(status["usable_for_resolution"], False)

    def test_unsupported_stage_is_refused_from_the_registry(self) -> None:
        """One registry-level case, not a matrix: the path is unreachable per seat.

        `supported_stages` is `_ALL_STAGES` for all four seats today, so looping over
        seats would assert nothing -- every one of them supports every stage. The
        refusal in `run_adapter()` can only be reached by narrowing a registry entry,
        which is what this does, and that is also the shape a future seat with a
        restricted stage set would take.
        """
        narrowed = dict(REVIEWERS)
        narrowed["codex"] = replace(
            REVIEWERS["codex"], supported_stages=frozenset({"review"})
        )
        with mock.patch(
            "ai_review.reviewers.REVIEWERS", MappingProxyType(narrowed)
        ), mock.patch(
            "ai_review.adapter_runner._run_adapter_process",
            side_effect=AssertionError("an unsupported stage must not spawn an adapter"),
        ):
            batch, status = self._run_seat(
                "codex", "critique", env={}, bundle=False, expected_exit=_EXIT_ERROR
            )

        self.assertEqual(status["status"], "config_error")
        self.assertEqual(status["error_class"], "ConfigError")
        self.assertIn(
            "does not support stage critique", str(status["error_message_redacted"])
        )
        # The refusal precedes the config load, so no effective-config digest exists
        # to stamp a batch with; a status alone is what keeps consensus from reading
        # a placeholder batch as a real (empty) result.
        self.assertIsNone(batch)


def _roster_enabling(reviewer: str) -> str:
    """A roster with ``reviewer`` on, padded to the three-enabled-seat floor."""
    others = [seat for seat in sorted(REVIEWER_IDS) if seat != reviewer]
    return ",".join([reviewer, *others[:2]])


def _roster_disabling(reviewer: str) -> str:
    """A roster with ``reviewer`` off and exactly the floor of seats enabled."""
    return ",".join(seat for seat in sorted(REVIEWER_IDS) if seat != reviewer)


class ReviewerConfigAndWorkflowParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = yaml.safe_load(_SHIPPED_CONFIG.read_text(encoding="utf-8"))

    def test_config_rejects_unknown_missing_and_removed_reviewer_keys(self) -> None:
        cases = []
        missing = copy.deepcopy(self.config)
        del missing["reviewers"]["cursor"]
        cases.append(missing)
        unknown = copy.deepcopy(self.config)
        unknown["reviewers"]["custom"] = copy.deepcopy(unknown["reviewers"]["cursor"])
        cases.append(unknown)
        for removed_key, value in (
            ("adapter", "adapters/codex.sh"),
            ("credential_variable", "OPENROUTER_API_KEY"),
        ):
            removed = copy.deepcopy(self.config)
            removed["reviewers"]["codex"][removed_key] = value
            cases.append(removed)

        for config in cases:
            with self.subTest(keys=config["reviewers"].keys()), self.assertRaises(
                ConfigError
            ):
                validate_config(config)

    def test_workflow_dispatch_and_config_match_registry(self) -> None:
        github = yaml.safe_load(_GITHUB_WORKFLOW.read_text(encoding="utf-8"))
        for stage in ("review", "critique"):
            self.assertEqual(
                set(github["jobs"][stage]["strategy"]["matrix"]["reviewer"]),
                REVIEWER_IDS,
            )
        self.assertEqual(set(self.config["reviewers"]), REVIEWER_IDS)

        gitlab = yaml.safe_load(_GITLAB_TEMPLATE.read_text(encoding="utf-8"))
        for stage in ("review", "critique"):
            job = gitlab[f"AI {stage}"]
            self.assertEqual(
                set(job["parallel"]["matrix"][0]["REVIEWER"]), REVIEWER_IDS
            )
            self.assertEqual(job["extends"], f".{stage}_template")
            self.assertEqual(gitlab[job["extends"]]["stage"], "ai_review")
            self.assertEqual(
                gitlab[job["extends"]]["script"],
                [f'/opt/ai-review/adapters/run_reviewer.sh "$REVIEWER" {stage}'],
            )

        review_needs = {item["job"] for item in gitlab[".review_template"]["needs"]}
        self.assertEqual(review_needs, {"prepare_ai_review"})
        critique_needs = {
            item["job"] for item in gitlab[".critique_template"]["needs"]
        }
        self.assertEqual(critique_needs, {"prepare_ai_review", "AI review"})
        consensus_needs = {item["job"] for item in gitlab["consensus_ai_review"]["needs"]}
        self.assertEqual(
            consensus_needs,
            {"prepare_ai_review", "AI review", "AI critique"},
        )


if __name__ == "__main__":
    unittest.main()
