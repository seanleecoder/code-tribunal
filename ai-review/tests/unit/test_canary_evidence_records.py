from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import nullcontext, redirect_stdout
from dataclasses import replace
from itertools import combinations
from pathlib import Path
from unittest import mock

import yaml

from tests.support.repository_script import load_repository_script

ROOT = Path(__file__).resolve().parents[3]
records = load_repository_script(
    "canary_evidence_records", ROOT / "scripts/canary_evidence_records.py"
)
release_inputs = load_repository_script(
    "check_release_inputs", ROOT / "scripts/check_release_inputs.py"
)
hostile_canary = load_repository_script(
    "gitlab_hostile_canary", ROOT / "scripts/gitlab_hostile_canary.py"
)
R = "a" * 40
BASE = "sha256:" + "b" * 64
REVIEWER = "sha256:" + "c" * 64
CANDIDATE = {
    "runtime_source": R,
    "base_image": f"ghcr.io/x/ai-review-base:2.0-{R}@{BASE}",
    "reviewer_image": f"ghcr.io/x/ai-review-reviewer:2.0-{R}@{REVIEWER}",
}
SEATS = ("claude", "codex", "cursor", "opencode")


def _panel(platform: str) -> dict:
    return {
        "schema_version": "candidate_canary_summary.v1",
        "platform": platform,
        "candidate": dict(CANDIDATE),
        "change_url": f"https://{platform}.example/change/1",
        "seats": {
            seat: {
                "review": {"status": "success", "model": f"m/{seat}"},
                "critique": {"status": "success"},
            }
            for seat in SEATS
        },
        "consensus": {"panel_status": "full", "resolution_eligible_reviewers": list(SEATS)},
        "posting": {"status": "success", "posted_thread_count": 2},
        "cleanup": "success",
    }


def _lifecycle(platform: str) -> dict:
    run_key, persist_key = (
        ("run_id", "persist_run_id") if platform == "github" else ("pipeline", "persist_pipeline")
    )
    state = {
        "state_note_id": 31,
        "issue_id": "d" * 64,
        "discussion_id": "thread-11",
        "root_note_id": 11,
        "status": "wontfix",
        "human_disposition": "wontfix",
    }
    return {
        "schema_version": "candidate_canary_lifecycle_summary.v1",
        "platform": platform,
        "candidate": dict(CANDIDATE),
        "change_url": f"https://{platform}.example/change/2",
        "passed": True,
        "steps": [
            {
                "name": "create",
                "passed": True,
                "observed": {
                    run_key: 201,
                    "thread": 11,
                    "status": "success",
                    "created_discussions": 1,
                },
            },
            {
                "name": "wontfix",
                "passed": True,
                "observed": {
                    run_key: 202,
                    persist_key: 203,
                    "state": state,
                    "status": "success",
                    "resolved_discussions": 1,
                    "persisted": {"status": "success", "resolved_discussions": 0},
                    "persisted_state": dict(state),
                },
            },
        ],
    }


def _hostile() -> dict:
    return {
        "schema_version": "candidate_canary_hostile_summary.v1",
        "platform": "gitlab",
        "candidate": dict(CANDIDATE),
        "change_url": "https://gitlab.example/change/3",
        "passed": True,
        "steps": [
            {
                "name": "credentials_withheld",
                "passed": True,
                "observed": {"GITLAB_TOKEN": "absent"},
            },
            {
                "name": "image_substituted",
                "passed": True,
                "observed": {"hostile_image_pulled": True},
            },
        ],
    }


def _run(**summaries: dict) -> object:
    full = {
        "panel-github": _panel("github"),
        "panel-gitlab": _panel("gitlab"),
        "lifecycle-github": _lifecycle("github"),
        "lifecycle-gitlab": _lifecycle("gitlab"),
        "hostile-gitlab": _hostile(),
    }
    full.update(summaries)
    present = {k: v for k, v in full.items() if v is not None}
    return records.CanaryRun(
        run_id="99",
        url="https://github.example/runs/99",
        date="2026-09-30",
        summaries=present,
        scanned_files=len(present),
    )


def _bad_summaries() -> dict[str, dict]:
    other = _panel("gitlab")
    other["candidate"] = dict(CANDIDATE, runtime_source="f" * 40)
    return {
        "one candidate": {"panel-gitlab": other},
        "did not pass": {"lifecycle-github": _lifecycle("github") | {"passed": False}},
        "incomplete": {"panel-github": _panel("github") | {"consensus": {"status": "incomplete"}}},
        "cleanup was failure": {"panel-github": _panel("github") | {"cleanup": "failure"}},
    }


def _summaries(campaigns: tuple[str, ...]) -> dict[str, dict]:
    return {
        key: value for key, value in _run().summaries.items() if key.partition("-")[0] in campaigns
    }


def _write_saved_records(output: Path) -> None:
    for filename, text in records.render_records(_run()).items():
        (output / filename).write_text(
            text.replace("None recorded.", "Saved context.").replace(
                "Status: passed", "Status: stale"
            )
        )


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}


def _waive_record(text: str) -> str:
    return "".join(
        line
        for line in text.splitlines(keepends=True)
        if not line.startswith(
            ("Release-runtime-source:", "Release-base-digest:", "Release-reviewer-digest:")
        )
    ).replace("Status: passed", "Status: waived\n\nRelease-evidence-waived: registered")


def _assert_release_valid(test: unittest.TestCase, root: Path, names) -> None:
    data = {
        "status": "active",
        "runtime_source": R,
        "images": {"base": {"digest": BASE}, "reviewer": {"digest": REVIEWER}},
        "verification": {"evidence_record_ids": sorted(names), "evidence_waivers": {}},
    }
    test.assertEqual(release_inputs.validate_evidence_records(data, root), [])


def _job(name: str, conclusion: str = "success", status: str = "completed") -> dict:
    return {"name": name, "conclusion": conclusion, "status": status}


def _metadata(campaigns: tuple[str, ...] = ("panel", "lifecycle", "hostile")) -> dict:
    jobs = [_job("verify-candidate")]
    for campaign, family in (("panel", "campaign"), ("lifecycle", "lifecycle")):
        if campaign in campaigns:
            jobs.extend(
                [
                    _job(f"{family} (github, CANDIDATE_CANARY_GITHUB_TOKEN, GH_TOKEN)"),
                    _job(f"{family} (gitlab, CANDIDATE_CANARY_GITLAB_TOKEN, GITLAB_CANARY_TOKEN)"),
                ]
            )
        else:
            jobs.append(_job(family, "skipped"))
    jobs.append(_job("hostile", "success" if "hostile" in campaigns else "skipped"))
    return {
        "url": "https://github.example/runs/99",
        "createdAt": "2026-09-30T00:00:00Z",
        "conclusion": "success",
        "status": "completed",
        "event": "workflow_dispatch",
        "headBranch": "main",
        "workflowDatabaseId": 42,
        "jobs": jobs,
    }


def _workflow() -> dict:
    return {"id": 42, "path": ".github/workflows/candidate-canary.yml"}


VIEW_CALL = mock.call(
    "run",
    "view",
    "99",
    "--repo",
    "github.com/seanleecoder/code-tribunal",
    "--json",
    "url,createdAt,status,conclusion,event,headBranch,workflowDatabaseId,jobs",
)
WORKFLOW_CALL = mock.call(
    "api", "--hostname", "github.com", "repos/seanleecoder/code-tribunal/actions/workflows/42"
)
ARTIFACTS_CALL = mock.call(
    "api",
    "--hostname",
    "github.com",
    "--paginate",
    "--slurp",
    "repos/seanleecoder/code-tribunal/actions/runs/99/artifacts?per_page=100",
)


def _artifacts(campaigns=("panel", "lifecycle", "hostile")) -> list[dict]:
    return [
        {"id": index, "name": artifact.name, "expired": False}
        for index, (key, artifact) in enumerate(records.DEMO_ARTIFACTS.items(), 1)
        if key.partition("-")[0] in campaigns
    ]


def _artifact_pages(artifacts=None, page_size=100) -> list[dict]:
    artifacts = _artifacts() if artifacts is None else artifacts
    return [
        {"total_count": len(artifacts), "artifacts": artifacts[start : start + page_size]}
        for start in range(0, max(1, len(artifacts)), page_size)
    ]


def _write_artifacts(directory: Path, summaries: dict) -> None:
    for key, summary in summaries.items():
        artifact = directory / records.DEMO_ARTIFACTS[key].name
        artifact.mkdir()
        (artifact / "summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _gh_response(
    meta: dict,
    workflow: dict,
    summaries: dict | None = None,
    damage=None,
    *,
    artifact_pages=None,
    after_artifact_pages=None,
):
    artifact_pages = _artifact_pages() if artifact_pages is None else artifact_pages
    artifact_reads = 0

    def respond(*args: str) -> str:
        nonlocal artifact_reads
        if args[:2] == ("run", "view"):
            return json.dumps(meta)
        if args == WORKFLOW_CALL.args:
            return json.dumps(workflow)
        if args == ARTIFACTS_CALL.args:
            pages = artifact_pages
            if artifact_reads and after_artifact_pages is not None:
                pages = after_artifact_pages
            artifact_reads += 1
            if isinstance(pages, Exception):
                raise pages
            return pages if isinstance(pages, str) else json.dumps(pages)
        if args[:2] == ("run", "download"):
            directory = Path(args[-1])
            if summaries is not None:
                _write_artifacts(directory, summaries)
            if damage is not None:
                damage(directory)
            return ""
        raise AssertionError(f"unexpected gh command: {args}")

    return respond


class RecordRenderingTests(unittest.TestCase):
    def test_every_record_binds_the_candidate_for_the_release_validator(self) -> None:
        rendered = records.render_records(_run())
        self.assertEqual(set(rendered), set(records.RECORDS))
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "docs/evidence"
            evidence.mkdir(parents=True)
            for name, text in rendered.items():
                (evidence / name).write_text(text, encoding="utf-8")
            _assert_release_valid(self, Path(tmp), rendered)
        for text in rendered.values():
            self.assertIn("## Operator notes", text)
            self.assertIn("scripts/canary_evidence_records.py", text)

    def test_observed_values_cannot_break_the_table(self) -> None:
        hostile = _hostile()
        hostile["steps"][0]["observed"] = {"note": "a|b"}
        lifecycle = _lifecycle("github")
        lifecycle["steps"][1]["observed"]["state"]["discussion_id"] = "a|b"
        rendered = records.render_records(
            _run(**{"hostile-gitlab": hostile, "lifecycle-github": lifecycle})
        )
        self.assertIn('"note": "a\\|b"', rendered["record-gitlab-hostile-mr.md"])
        self.assertIn('"discussion_id": "a\\|b"', rendered["record-github-current-image.md"])

    def test_lifecycle_retains_consumer_runs_threads_and_persistence_evidence(self) -> None:
        rendered = records.render_records(_run())
        for platform, run_key, persist_key in (
            ("github", "run_id", "persist_run_id"),
            ("gitlab", "pipeline", "persist_pipeline"),
        ):
            with self.subTest(platform=platform):
                text = rendered[f"record-{platform}-current-image.md"]
                create = next(line for line in text.splitlines() if line.startswith("| `create`"))
                wontfix = next(line for line in text.splitlines() if line.startswith("| `wontfix`"))
                self.assertIn(f"{run_key}=201, thread=11", create)
                self.assertIn("created_discussions=1", create)
                self.assertIn(f"{run_key}=202, {persist_key}=203", wontfix)
                observed = _lifecycle(platform)["steps"][1]["observed"]
                for key in ("state", "persisted_state", "persisted"):
                    self.assertIn(f"{key}={json.dumps(observed[key], sort_keys=True)}", wontfix)

    def test_hostile_producer_summary_renders_its_observations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "candidate": CANDIDATE,
                        "change_url": _hostile()["change_url"],
                        "mr_iid": "5",
                        "template_sha": R,
                    }
                ),
                encoding="utf-8",
            )
            summary = Path(tmp) / "summary.json"
            args = argparse.Namespace(state=state, summary_out=summary, timeout_seconds=60)
            with mock.patch.object(
                hostile_canary.HostileProbe,
                "steps",
                return_value=[
                    ("credentials_withheld", lambda: {"GITLAB_TOKEN": "absent"}),
                    ("image_substituted", lambda: {"hostile_image_pulled": True}),
                ],
            ):
                self.assertEqual(hostile_canary.run_probe(args), 0)
            produced = json.loads(summary.read_text(encoding="utf-8"))
        text = records.render_records(_run(**{"hostile-gitlab": produced}))[
            "record-gitlab-hostile-mr.md"
        ]
        self.assertIn('| `credentials_withheld` | passed | {"GITLAB_TOKEN": "absent"} |', text)
        self.assertIn('| `image_substituted` | passed | {"hostile_image_pulled": true} |', text)

    def test_an_unpinned_image_refuses_the_record(self) -> None:
        lifecycle = _lifecycle("github")
        lifecycle["candidate"] = dict(CANDIDATE, base_image="ghcr.io/x/ai-review-base:2.0")
        run = _run(
            **{key: None for key in records.DEMO_ARTIFACTS} | {"lifecycle-github": lifecycle}
        )
        with self.assertRaisesRegex(records.RecordError, "not a digest-pinned"):
            records.render_records(run)


class RecordRegenerationTests(unittest.TestCase):
    def _regenerate(self, output: Path, campaigns=("panel", "lifecycle", "hostile")):
        summaries = _summaries(campaigns)
        stdout = io.StringIO()
        with (
            mock.patch.object(
                records,
                "_gh",
                side_effect=_gh_response(
                    _metadata(campaigns),
                    _workflow(),
                    summaries,
                    artifact_pages=_artifact_pages(_artifacts(campaigns)),
                ),
            ),
            redirect_stdout(stdout),
        ):
            result = records.main(["99", "--out", str(output)])
        return result, stdout.getvalue()

    def test_same_run_preserves_each_records_notes_and_refreshes_generated_content(self) -> None:
        rendered = records.render_records(_run())
        expected = {
            name: text.replace("\nNone recorded.\n\n", f"\nOperator context for {name}.\n\n")
            for name, text in rendered.items()
        }
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "docs/evidence"
            output.mkdir(parents=True)
            for name, text in expected.items():
                stale = text.replace("Status: passed", "Status: stale").replace(
                    "Scoped pass", "Outdated verdict"
                )
                (output / name).write_bytes(stale.encode("utf-8"))
            for _ in range(2):
                self.assertEqual(self._regenerate(output)[0], 0)
                self.assertEqual(
                    _snapshot(output),
                    {name: text.encode("utf-8") for name, text in expected.items()},
                )
            _assert_release_valid(self, Path(tmp), expected)

    def test_notes_preserve_empty_bodies_whitespace_unicode_and_markdown_examples(self) -> None:
        name = "record-candidate-canary.md"
        template = records.render_records(_run())[name]
        bodies = (
            "",
            "\nNone recorded.\n\n",
            "\r\n\r\nRésumé — caveats  \r\n\t- item\r\n### Detail\r\n\r\n",
            "\n- first  \n\t- nested\n\n### Caveat\n\nLast paragraph.\n\n",
            "\n```markdown\n## Operator notes\n## Verdict\n```\n\n",
            "\n````markdown\n```\n## Verdict\n````\n\n",
            "\n   ~~~markdown\n## Operator notes\n## Verdict\n   ~~~~\n\n",
            "\n- Candidate Canary run: [`100`](https://other.example/runs/100), 2026-09-30\n"
            f"Release-runtime-source: {'f' * 40}\n\n",
        )
        for body in bodies:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                expected = template.replace("\nNone recorded.\n\n", body)
                # Both mixed-newline notes and an entirely CRLF record retain their body.
                existing = expected
                if body.startswith("\r\n"):
                    existing = template.replace("\n", "\r\n").replace(
                        "\r\nNone recorded.\r\n\r\n", body
                    )
                (output / name).write_bytes(existing.encode("utf-8"))
                self.assertEqual(self._regenerate(output)[0], 0)
                self.assertEqual((output / name).read_bytes(), expected.encode("utf-8"))

    def test_new_historical_and_other_run_records_use_default_notes(self) -> None:
        old_run = replace(_run(), run_id="98", url="https://github.example/runs/98")
        existing = {
            "new files": {},
            "historical record without notes": {
                "record-candidate-canary.md": "# Historical evidence\n\n## Audit\n\n"
                "Historical context.\n\n## Verdict\n\nPass.\n"
            },
            "another run with the same candidate": {
                name: text.replace("None recorded.", "Notes for run 98.")
                for name, text in records.render_records(old_run).items()
            },
        }
        expected = {
            name: text.encode("utf-8") for name, text in records.render_records(_run()).items()
        }
        for label, files in existing.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                for name, text in files.items():
                    (output / name).write_text(text)
                self.assertEqual(self._regenerate(output)[0], 0)
                self.assertEqual(_snapshot(output), expected)

    def test_another_run_replaces_waived_records_and_leaves_skipped_records_unchanged(self) -> None:
        old_run = replace(_run(), run_id="98", url="https://github.example/runs/98")
        old_records = records.render_records(old_run)
        for campaigns in (("panel", "lifecycle", "hostile"), ("lifecycle",)):
            with self.subTest(campaigns=campaigns), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "docs/evidence"
                output.mkdir(parents=True)
                for name, text in old_records.items():
                    (output / name).write_text(
                        _waive_record(text).replace("None recorded.", "PRIVATE CONTEXT for run 98.")
                    )
                before = _snapshot(output)
                summaries = _summaries(campaigns)
                expected = records.render_records(
                    replace(_run(), summaries=summaries, scanned_files=len(summaries))
                )
                result, stdout = self._regenerate(output, campaigns)
                self.assertEqual(result, 0, stdout)
                self.assertNotIn("PRIVATE CONTEXT", stdout)
                self.assertEqual(
                    _snapshot(output),
                    {name: text.encode("utf-8") for name, text in expected.items()}
                    | {name: text for name, text in before.items() if name not in expected},
                )
                _assert_release_valid(self, Path(tmp), expected)

    def test_partial_run_preserves_selected_notes_and_does_not_read_skipped_records(self) -> None:
        rendered = records.render_records(_run())
        partial = records.render_records(
            _run(**{"panel-github": None, "panel-gitlab": None, "hostile-gitlab": None})
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            selected = {}
            for platform in ("github", "gitlab"):
                name = f"record-{platform}-current-image.md"
                selected[name] = partial[name].replace("None recorded.", f"{platform} context.")
                (output / name).write_text(
                    rendered[name].replace("None recorded.", f"{platform} context.")
                )
            skipped = {"record-candidate-canary.md", "record-gitlab-hostile-mr.md"}
            for name in skipped:
                # These would fail decoding if the partial run inspected them.
                (output / name).write_bytes(b"\xff skipped evidence\n")
            result, stdout = self._regenerate(output, ("lifecycle",))
            self.assertEqual(result, 0)
            for name, text in selected.items():
                self.assertEqual((output / name).read_bytes(), text.encode("utf-8"))
            for name in skipped:
                self.assertEqual((output / name).read_bytes(), b"\xff skipped evidence\n")
                self.assertIn(f"skipped {name}", stdout)

    def test_ambiguous_notes_or_identity_refuse_before_any_record_changes(self) -> None:
        name = "record-gitlab-hostile-mr.md"
        template = records.render_records(_run())[name]
        identity = "- Candidate Canary run: [`99`](https://github.example/runs/99), 2026-09-30"
        old_identity = identity.replace("99", "98")
        old_waived = _waive_record(template.replace(identity, old_identity))
        cases = {
            "changed URL": template.replace("https://github.example/runs/99", "https://other/99"),
            "missing run": template.replace(identity, ""),
            "duplicate run": template.replace(identity, identity + "\n" + identity),
            "missing notes": template.replace("## Operator notes", "## Removed notes"),
            "duplicate notes": template.replace(
                "## Operator notes", "## Operator notes\n\n## Operator notes"
            ),
            "missing verdict": template.replace("## Verdict", "## Removed verdict"),
            "duplicate verdict": template + "\n## Verdict\n\nAnother verdict.\n",
            "extra section": template.replace("None recorded.", "## Unexpected section\n\nNotes."),
            "unclosed fence": template.replace("None recorded.", "```markdown\nNotes."),
            "unknown identity": "## Operator notes\n\nNotes.\n\n## Verdict\n\nPass.\n",
            "waived same run": _waive_record(template),
            "waived missing run": old_waived.replace(old_identity, ""),
            "waived duplicate run": old_waived.replace(
                old_identity, old_identity + "\n" + old_identity
            ),
        }
        for field, value in (
            ("runtime-source", R),
            ("base-digest", BASE),
            ("reviewer-digest", REVIEWER),
        ):
            binding = f"Release-{field}: {value}"
            cases[f"changed {field}"] = template.replace(binding, binding[:-1] + "f")
            cases[f"missing {field}"] = template.replace(binding, "")
            cases[f"duplicate {field}"] = template.replace(binding, binding + "\n" + binding)
        for label, damaged in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                _write_saved_records(output)
                (output / name).write_text(damaged.replace("None recorded.", "PRIVATE CONTEXT"))
                before = _snapshot(output)
                result, stdout = self._regenerate(output)
                self.assertEqual(result, 1)
                self.assertIn(name, stdout)
                self.assertNotIn("PRIVATE CONTEXT", stdout)
                self.assertEqual(_snapshot(output), before)

    def test_unreadable_selected_records_refuse_before_any_record_changes(self) -> None:
        name = "record-gitlab-hostile-mr.md"
        original_read = Path.read_bytes

        def denied_read(path: Path) -> bytes:
            if path.name == name:
                raise PermissionError("PRIVATE ERROR CONTENT")
            return original_read(path)

        damages = {
            "invalid UTF-8": lambda target: target.write_bytes(b"\xff"),
            "directory": lambda target: (target.unlink(), target.mkdir()),
            "permission": lambda target: None,
        }
        for damage, apply in damages.items():
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp)
                _write_saved_records(output)
                target = output / name
                apply(target)
                before = _snapshot(output)
                reads = (
                    mock.patch.object(Path, "read_bytes", denied_read)
                    if damage == "permission"
                    else nullcontext()
                )
                with reads:
                    result, stdout = self._regenerate(output)
                self.assertEqual(result, 1)
                self.assertIn(name, stdout)
                self.assertNotIn("PRIVATE ERROR CONTENT", stdout)
                self.assertEqual(
                    _snapshot(output),
                    before,
                )
                if damage == "directory":
                    self.assertTrue(target.is_dir())


class RecordLoadingTests(unittest.TestCase):
    def test_demo_artifacts_match_the_canary_workflow_uploads(self) -> None:
        workflow = yaml.load(
            (ROOT / records.CANARY_WORKFLOW).read_text(encoding="utf-8"), Loader=yaml.BaseLoader
        )
        uploads = set()
        for job_id, job in workflow["jobs"].items():
            include = job.get("strategy", {}).get("matrix", {}).get("include", [])
            platforms = [entry["platform"] for entry in include] or [None]
            for step in job["steps"]:
                if not step.get("uses", "").startswith("actions/upload-artifact@"):
                    continue
                for platform in platforms:
                    name = step["with"]["name"]
                    if platform is not None:
                        name = name.replace("${{ matrix.platform }}", platform)
                    uploads.add((name, job_id))
        self.assertEqual(
            uploads, {(artifact.name, artifact.job) for artifact in records.DEMO_ARTIFACTS.values()}
        )

    def _refuses(
        self,
        *,
        meta=None,
        workflow=None,
        summaries=None,
        damage=None,
        artifact_pages=None,
        after_artifact_pages=None,
        downloaded=True,
        error="ERROR:",
    ) -> str:
        meta = _metadata() if meta is None else meta
        workflow = _workflow() if workflow is None else workflow
        summaries = _run().summaries if summaries is None else summaries
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "evidence"
            output.mkdir()
            for name in records.RECORDS:
                (output / name).write_bytes(f"existing {name}\n".encode())
            before = _snapshot(output)
            stdout = io.StringIO()
            with (
                mock.patch.object(
                    records,
                    "_gh",
                    side_effect=_gh_response(
                        meta,
                        workflow,
                        summaries,
                        damage,
                        artifact_pages=artifact_pages,
                        after_artifact_pages=after_artifact_pages,
                    ),
                ) as gh,
                mock.patch.object(records, "scan", wraps=records.scan) as scan,
                redirect_stdout(stdout),
            ):
                self.assertEqual(records.main(["99", "--out", str(output)]), 1)
            self.assertEqual(_snapshot(output), before)
            self.assertEqual(gh.call_args_list[0], VIEW_CALL)
            downloads = [call for call in gh.call_args_list if call.args[:2] == ("run", "download")]
            self.assertEqual(len(downloads), int(downloaded))
            if not downloaded:
                scan.assert_not_called()
        self.assertRegex(stdout.getvalue(), error)
        return stdout.getvalue()

    def test_successful_full_and_partial_runs_render_their_campaigns(self) -> None:
        campaign_records = {
            "panel": {"record-candidate-canary.md"},
            "lifecycle": {"record-github-current-image.md", "record-gitlab-current-image.md"},
            "hostile": {"record-gitlab-hostile-mr.md"},
        }
        for size in (1, 2, 3):
            for campaigns in combinations(campaign_records, size):
                summaries = _summaries(campaigns)
                filenames = set().union(*(campaign_records[c] for c in campaigns))
                with (
                    self.subTest(campaigns=campaigns),
                    tempfile.TemporaryDirectory() as tmp,
                    mock.patch.object(
                        records,
                        "_gh",
                        side_effect=_gh_response(
                            _metadata(campaigns),
                            _workflow(),
                            summaries,
                            artifact_pages=_artifact_pages(_artifacts(campaigns), page_size=1),
                        ),
                    ) as gh,
                ):
                    directory = Path(tmp)
                    loaded = records.load_run("99", directory)
                    self.assertEqual(set(records.render_records(loaded)), filenames)
                    self.assertEqual(loaded.summaries, summaries)
                    self.assertEqual(loaded.scanned_files, len(summaries))
                    self.assertEqual(
                        gh.call_args_list,
                        [
                            VIEW_CALL,
                            WORKFLOW_CALL,
                            ARTIFACTS_CALL,
                            mock.call(
                                "run",
                                "download",
                                "99",
                                "--repo",
                                "github.com/seanleecoder/code-tribunal",
                                "--dir",
                                str(directory),
                            ),
                            ARTIFACTS_CALL,
                        ],
                    )

    def test_unsuccessful_runs_refuse_even_with_passing_summaries(self) -> None:
        cases = [_metadata() | {"conclusion": conclusion} for conclusion in ("failure", "", None)]
        cases.append({key: value for key, value in _metadata().items() if key != "conclusion"})
        for meta in cases:
            with self.subTest(meta=meta):
                self._refuses(
                    meta=meta, downloaded=False, error="run 99 conclusion is .*expected 'success'"
                )

    def test_wrong_or_incomplete_run_provenance_refuses_before_downloading(self) -> None:
        cases = [
            _metadata() | {field: value}
            for field, value in (
                ("event", "pull_request"),
                ("event", "push"),
                ("headBranch", "feature/forged-canary"),
                ("status", "in_progress"),
                ("workflowDatabaseId", "42"),
                ("workflowDatabaseId", True),
                ("workflowDatabaseId", 0),
                ("url", ""),
                ("createdAt", None),
            )
        ]
        cases.extend(
            {key: value for key, value in _metadata().items() if key != field}
            for field in ("event", "headBranch", "status", "workflowDatabaseId", "url", "createdAt")
        )
        for meta in cases:
            with self.subTest(meta=meta):
                self._refuses(meta=meta, downloaded=False)

    def test_another_workflow_cannot_forge_passed_records(self) -> None:
        for workflow in (
            {"id": 42, "path": ".github/workflows/unprotected.yml"},
            {"id": 43, "path": ".github/workflows/candidate-canary.yml"},
            {"id": 42},
            {"path": ".github/workflows/candidate-canary.yml"},
        ):
            with self.subTest(workflow=workflow):
                self._refuses(workflow=workflow, downloaded=False, error="not the canonical")

    def test_missing_duplicate_or_unsuccessful_verification_refuses(self) -> None:
        for verification in (
            [],
            [_job("verify-candidate"), _job("verify-candidate")],
            [_job("verify-candidate", "failure")],
            [_job("verify-candidate", "skipped")],
            [_job("verify-candidate", status="in_progress")],
            [{"name": "verify-candidate", "conclusion": "success"}],
        ):
            with self.subTest(verification=verification):
                meta = _metadata()
                meta["jobs"] = verification + meta["jobs"][1:]
                self._refuses(meta=meta, downloaded=False, error="verify-candidate")
        for jobs in (None, {}, [None], [{"conclusion": "success"}]):
            with self.subTest(jobs=jobs):
                self._refuses(
                    meta=_metadata() | {"jobs": jobs}, downloaded=False, error="jobs list"
                )

    def test_campaign_job_metadata_must_identify_complete_successful_campaigns(self) -> None:
        full = _metadata()["jobs"]
        cases = [full[:index] + full[index + 1 :] for index in range(1, len(full))]
        cases.extend(full + [job] for job in full[1:])
        for index in range(1, len(full)):
            for conclusion in ("failure", "skipped", "neutral", None):
                if full[index]["name"] == "hostile" and conclusion == "skipped":
                    continue
                cases.append(
                    full[:index] + [full[index] | {"conclusion": conclusion}] + full[index + 1 :]
                )
        cases.extend(
            [
                full[:1] + [_job("campaign")] + full[3:],
                full[:1] + [_job("campaign", "skipped")] + full[1:],
                full[:1] + [_job("campaign (other)"), full[2]] + full[3:],
                full[:1] + [_job("campaign (github)"), _job("campaign (github)")] + full[3:],
                full[:1] + [full[1] | {"status": "in_progress"}] + full[2:],
                _metadata(())["jobs"],
            ]
        )
        for jobs in cases:
            with self.subTest(jobs=jobs):
                self._refuses(meta=_metadata() | {"jobs": jobs}, downloaded=False)

    def test_matrix_names_without_extra_values_are_accepted(self) -> None:
        meta = _metadata()
        for job in meta["jobs"]:
            if "," in job["name"]:
                job["name"] = job["name"].split(",")[0] + ")"
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                records, "_gh", side_effect=_gh_response(meta, _workflow(), _run().summaries)
            ),
        ):
            self.assertEqual(
                set(records.render_records(records.load_run("99", Path(tmp)))), set(records.RECORDS)
            )

    def test_missing_summary_for_any_successful_job_never_overwrites_evidence(self) -> None:
        for missing in _run().summaries:
            with self.subTest(missing=missing):
                self._refuses(
                    summaries={k: v for k, v in _run().summaries.items() if k != missing},
                    error=f"{missing} requires exactly one summary JSON file; found 0",
                )

    def test_empty_or_ambiguous_summary_artifacts_never_overwrite_evidence(self) -> None:
        for key, artifact in records.DEMO_ARTIFACTS.items():
            with self.subTest(key=key, kind="empty"):
                self._refuses(
                    summaries={k: v for k, v in _run().summaries.items() if k != key},
                    damage=lambda directory, name=artifact.name: (directory / name).mkdir(),
                    error=f"{key} requires exactly one summary JSON file; found 0",
                )
            with self.subTest(key=key, kind="ambiguous"):
                self._refuses(
                    damage=lambda directory, name=artifact.name: (
                        directory / name / "duplicate.json"
                    ).write_text("{}"),
                    error=f"{key} requires exactly one summary JSON file; found 2",
                )

    def test_malformed_summary_artifacts_never_overwrite_evidence(self) -> None:
        for key, artifact in records.DEMO_ARTIFACTS.items():
            for payload in (b"{", b"[]", b"null", b"{}", b"\xff"):
                with self.subTest(key=key, payload=payload):
                    self._refuses(
                        damage=lambda directory, name=artifact.name, payload=payload: (
                            directory / name / "summary.json"
                        ).write_bytes(payload),
                    )

    def test_artifacts_for_skipped_campaigns_are_rejected(self) -> None:
        for campaigns in (("panel", "lifecycle"), ("panel", "hostile"), ("lifecycle", "hostile")):
            with self.subTest(campaigns=campaigns):
                self._refuses(
                    meta=_metadata(campaigns),
                    downloaded=False,
                    error="despite its campaign being skipped",
                )

    def test_downloaded_artifacts_for_skipped_campaigns_are_still_rejected(self) -> None:
        self._refuses(
            meta=_metadata(("panel",)),
            artifact_pages=_artifact_pages(_artifacts(("panel",))),
            error="despite its campaign being skipped",
        )

    def test_green_rerun_refuses_duplicate_names_before_cli_discards_them(self) -> None:
        for index, artifact in enumerate(_artifacts()):
            for expired in (False, True):
                entries = _artifacts()
                entries[index] = artifact | {"expired": expired}
                entries.append(artifact | {"id": artifact["id"] + 100})
                with self.subTest(name=artifact["name"], expired=expired):
                    # The fake CLI extracts only one JSON for each summary name.
                    stdout = self._refuses(
                        artifact_pages=_artifact_pages(entries),
                        downloaded=False,
                        error=f"{artifact['name']} requires exactly one artifact; found 2",
                    )
                    self.assertIn(f"IDs: {[artifact['id'], artifact['id'] + 100]}", stdout)
                    self.assertIn("a new run ID", stdout)

    def test_missing_or_expired_artifact_metadata_refuses_before_downloading(self) -> None:
        for index, artifact in enumerate(_artifacts()):
            with self.subTest(name=artifact["name"], kind="missing"):
                entries = _artifacts()
                del entries[index]
                self._refuses(
                    artifact_pages=_artifact_pages(entries),
                    downloaded=False,
                    error=f"{artifact['name']} requires exactly one artifact; found 0",
                )
            with self.subTest(name=artifact["name"], kind="expired"):
                entries = _artifacts()
                entries[index]["expired"] = True
                self._refuses(
                    artifact_pages=_artifact_pages(entries), downloaded=False, error="has expired"
                )

    def test_malformed_or_incomplete_artifact_metadata_refuses_before_downloading(self) -> None:
        entries = _artifacts()
        count = len(entries)
        cases = [
            "{",
            "null",
            {},
            [],
            [None],
            [{}],
            [{"total_count": True, "artifacts": entries}],
            [{"total_count": -1, "artifacts": entries}],
            [{"total_count": str(count), "artifacts": entries}],
            [{"total_count": count, "artifacts": {}}],
            [{"total_count": count, "artifacts": entries[:-1]}],
            [
                {"total_count": count, "artifacts": entries[:1]},
                {"total_count": count + 1, "artifacts": entries[1:]},
            ],
            _artifact_pages(entries + [entries[0]], page_size=1),
            '[{"total_count": 0, "total_count": 5, "artifacts": []}]',
        ]
        for field, values in (
            ("id", (None, True, 0, -1, "1")),
            ("name", (None, "", 1)),
            ("expired", (None, 0, "false")),
        ):
            cases.extend(
                _artifact_pages([entries[0] | {field: value}, *entries[1:]]) for value in values
            )
            cases.append(
                _artifact_pages(
                    [
                        {key: value for key, value in entries[0].items() if key != field},
                        *entries[1:],
                    ]
                )
            )
        cases.append(_artifact_pages([None, *entries[1:]]))
        for pages in cases:
            with self.subTest(pages=pages):
                self._refuses(artifact_pages=pages, downloaded=False, error="artifact metadata")

    def test_artifact_inventory_changes_during_download_never_overwrite_records(self) -> None:
        entries = _artifacts()
        changed = [entries[0] | {"id": 101}, *entries[1:]]
        renamed = [*entries, {"id": 101, "name": "diagnostic", "expired": False}]
        cases = [
            _artifact_pages(changed),
            _artifact_pages(renamed),
            _artifact_pages(entries[1:]),
            _artifact_pages([entries[0] | {"expired": True}, *entries[1:]]),
            _artifact_pages([*entries, entries[0] | {"id": 101}]),
            "null",
        ]
        for pages in cases:
            with self.subTest(pages=pages):
                self._refuses(after_artifact_pages=pages)

    def test_inventory_comparison_ignores_page_order_and_unrelated_metadata(self) -> None:
        entries = _artifacts() + [{"id": 101, "name": "diagnostic", "expired": False}]
        after = [entry | {"updated_at": "2026-10-01T00:00:00Z"} for entry in reversed(entries)]
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                records,
                "_gh",
                side_effect=_gh_response(
                    _metadata(),
                    _workflow(),
                    _run().summaries,
                    artifact_pages=_artifact_pages(entries),
                    after_artifact_pages=_artifact_pages(after, page_size=1),
                ),
            ),
        ):
            loaded = records.load_run("99", Path(tmp))
            self.assertEqual(loaded.summaries, _run().summaries)

    def test_artifact_api_failures_never_overwrite_records(self) -> None:
        failure = records.RecordError("artifact API unavailable")
        self._refuses(artifact_pages=failure, downloaded=False, error="artifact API unavailable")
        self._refuses(after_artifact_pages=failure, error="artifact API unavailable")

    def test_bad_campaign_results_never_overwrite_other_records(self) -> None:
        for message, bad in _bad_summaries().items():
            with self.subTest(message=message):
                self._refuses(summaries=_run(**bad).summaries, error=message)

    def test_leak_scan_checks_all_downloaded_files_before_writing(self) -> None:
        stdout = self._refuses(
            damage=lambda directory: (directory / "diagnostic.txt").write_text("ghp_" + "x" * 32),
            error="leak scan flagged",
        )
        self.assertNotIn("ghp_", stdout)


if __name__ == "__main__":
    unittest.main()
