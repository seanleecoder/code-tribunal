from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from ai_review.canonical import sha256_hex
from ai_review.gitlab_client import MergeRequestVersion
from ai_review.input_bundle import (
    BundleError,
    _copy_regular_file_nofollow,
    _enforce_diff_limits,
    _external_fork_secrets_blocked,
    _github_checkout_head,
    _github_pull_request_version,
    _resolve_github_pull_request,
    _symlinks_touched_by_diff,
    copy_repo_snapshot,
    prepare_github_bundle,
    prepare_gitlab_bundle,
    prepare_local_bundle,
)
from ai_review.platform import ReviewPlatformError
from ai_review.platform.github import GitHubReviewPlatform

_REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "review.yaml"


def _github_platform_mock() -> mock.Mock:
    return mock.Mock(spec=GitHubReviewPlatform)


def _diff_with_files(count: int) -> str:
    chunks = []
    for index in range(count):
        chunks.append(f"diff --git a/f{index}.py b/f{index}.py")
        chunks.append(f"--- a/f{index}.py")
        chunks.append(f"+++ b/f{index}.py")
        chunks.append("@@ -0,0 +1 @@")
        chunks.append("+x = 1")
    return "\n".join(chunks) + "\n"


class InputBundleLimitTests(unittest.TestCase):
    def test_within_limits_does_not_raise(self) -> None:
        _enforce_diff_limits(
            _diff_with_files(2),
            {"limits": {"max_diff_bytes": 250000, "max_files": 200}},
        )

    def test_oversized_diff_bytes_raises(self) -> None:
        big = "diff --git a/f.py b/f.py\n" + ("+x = 1\n" * 1000)
        with self.assertRaisesRegex(BundleError, "max_diff_bytes"):
            _enforce_diff_limits(big, {"limits": {"max_diff_bytes": 100}})

    def test_too_many_files_raises(self) -> None:
        with self.assertRaisesRegex(BundleError, "max_files"):
            _enforce_diff_limits(_diff_with_files(5), {"limits": {"max_files": 3}})

    def test_defaults_apply_when_limits_absent(self) -> None:
        # Missing limits fall back to the documented defaults (250000 bytes / 200 files).
        _enforce_diff_limits(_diff_with_files(1), {})

    def test_external_fork_secret_gate_blocks_by_default(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"CI_PROJECT_ID": "1", "CI_MERGE_REQUEST_SOURCE_PROJECT_ID": "2"},
            clear=True,
        ):
            reason = _external_fork_secrets_blocked({"security": {}})
        self.assertIsNotNone(reason)
        self.assertIn("allow_external_fork_secrets is false", reason or "")

    def test_external_fork_secret_gate_allows_explicit_opt_in(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"CI_PROJECT_ID": "1", "CI_MERGE_REQUEST_SOURCE_PROJECT_ID": "2"},
            clear=True,
        ):
            reason = _external_fork_secrets_blocked(
                {"security": {"allow_external_fork_secrets": True}}
            )
        self.assertIsNone(reason)

    def test_prepare_state_backend_fails_closed_when_current_user_unavailable(self) -> None:
        class BrokenUserClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def fetch_version(self, project_id: str, change_id: str) -> MergeRequestVersion:
                return MergeRequestVersion("base", "start", "head")

            def fetch_diff(self, project_id: str, change_id: str) -> str:
                return "diff --git a/f.py b/f.py\n"

            def current_user_id(self) -> int | None:
                return None

            def current_user(self) -> dict[str, object]:
                raise RuntimeError("user lookup failed")

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.dict(
                "os.environ",
                {
                    "CI_API_V4_URL": "https://gitlab.example/api/v4",
                    "CI_PROJECT_ID": "1",
                    "CI_MERGE_REQUEST_IID": "2",
                    "GITLAB_TOKEN": "token",
                },
                clear=True,
            ),
            mock.patch(
                "ai_review.input_bundle.load_config",
                return_value={
                    "state": {
                        "backend": "gitlab_mr_state_note",
                        "fail_closed_on_load_error": True,
                    }
                },
            ),
            mock.patch(
                "ai_review.input_bundle.create_runtime_platform",
                return_value=BrokenUserClient(),
            ),
            mock.patch("ai_review.input_bundle.shutil.copy2"),
            mock.patch("ai_review.input_bundle.shutil.copytree"),
            mock.patch("ai_review.input_bundle.copy_repo_snapshot"),
            mock.patch("ai_review.input_bundle._file_sha256", return_value="0" * 64),
            mock.patch("ai_review.input_bundle._directory_sha256", return_value="1" * 64),
            self.assertRaisesRegex(BundleError, "current_user"),
        ):
            prepare_gitlab_bundle(Path("ai-review/config/review.yaml"), Path(tmpdir))

    def test_prepare_surfaces_truncated_diff_as_bundle_error(self) -> None:
        class TruncatedDiffClient:
            def fetch_version(self, project_id: str, change_id: str) -> MergeRequestVersion:
                return MergeRequestVersion("base", "start", "head")

            def fetch_diff(self, project_id: str, change_id: str) -> str:
                raise ReviewPlatformError(
                    "merge request diff is truncated or collapsed for big.py; "
                    "refusing to review an incomplete diff"
                )

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.dict(
                "os.environ",
                {
                    "CI_API_V4_URL": "https://gitlab.example/api/v4",
                    "CI_PROJECT_ID": "1",
                    "CI_MERGE_REQUEST_IID": "2",
                    "GITLAB_TOKEN": "token",
                },
                clear=True,
            ),
            mock.patch(
                "ai_review.input_bundle.load_config",
                return_value={"state": {"backend": "none"}},
            ),
            mock.patch(
                "ai_review.input_bundle.create_runtime_platform",
                return_value=TruncatedDiffClient(),
            ),
            self.assertRaisesRegex(BundleError, "truncated or collapsed"),
        ):
            prepare_gitlab_bundle(Path("ai-review/config/review.yaml"), Path(tmpdir))


class GitHubPullRequestResolutionTests(unittest.TestCase):
    @staticmethod
    def _pull_request(*, number: int = 7, source_repo: str = "octo/repo") -> dict[str, object]:
        return {
            "number": number,
            "head": {
                "ref": "feature",
                "sha": "1" * 40,
                "repo": {"full_name": source_repo},
            },
            "base": {"ref": "main", "sha": "0" * 40},
        }

    def test_automatic_handoff_fetches_current_api_metadata(self) -> None:
        client = mock.Mock()
        expected = self._pull_request()
        client.fetch_pull_request.return_value = expected
        with mock.patch.dict(
            "os.environ", {"AI_REVIEW_GITHUB_PR_NUMBER": "7"}, clear=True
        ):
            actual = _resolve_github_pull_request(client, "octo/repo")

        self.assertEqual(actual, expected)
        client.fetch_pull_request.assert_called_once_with("octo/repo", "7")

    def test_manual_dispatch_fetches_requested_pull_request(self) -> None:
        client = mock.Mock()
        expected = self._pull_request(number=32)
        client.fetch_pull_request.return_value = expected
        with mock.patch.dict("os.environ", {"AI_REVIEW_GITHUB_PR_NUMBER": "32"}, clear=True):
            actual = _resolve_github_pull_request(client, "octo/repo")

        self.assertEqual(actual, expected)
        client.fetch_pull_request.assert_called_once_with("octo/repo", "32")

    def test_version_is_built_from_resolved_pull_request_metadata(self) -> None:
        version = _github_pull_request_version(self._pull_request())

        self.assertEqual(version.base_sha, "0" * 40)
        self.assertEqual(version.head_sha, "1" * 40)

    def test_version_requires_both_endpoint_shas(self) -> None:
        pull_request = self._pull_request()
        pull_request["head"] = {"repo": {"full_name": "octo/repo"}}

        with self.assertRaisesRegex(BundleError, "base.sha and head.sha"):
            _github_pull_request_version(pull_request)

    def test_manual_prepare_uses_immutable_diff_and_records_revision_fields(self) -> None:
        client = _github_platform_mock()
        client.fetch_pull_request.return_value = self._pull_request(number=32)
        client.fetch_comparison_diff.return_value = "diff --git a/f.py b/f.py\n"
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.dict(
                "os.environ",
                {
                    "GITHUB_REPOSITORY": "octo/repo",
                    "AI_REVIEW_GITHUB_PR_NUMBER": "32",
                    "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA": "1" * 40,
                    "GITHUB_RUN_ID": "100",
                    "GITHUB_RUN_ATTEMPT": "1",
                },
                clear=True,
            ),
            mock.patch("ai_review.input_bundle.load_config", return_value={}),
            mock.patch("ai_review.input_bundle.create_runtime_platform", return_value=client),
            mock.patch(
                "ai_review.input_bundle._github_checkout_head", return_value="1" * 40
            ),
            mock.patch(
                "ai_review.input_bundle._load_platform_state",
                side_effect=lambda _client, _config, state, **_kwargs: state,
            ),
            mock.patch("ai_review.input_bundle.shutil.copy2"),
            mock.patch("ai_review.input_bundle.shutil.copytree"),
            mock.patch("ai_review.input_bundle.copy_repo_snapshot"),
            mock.patch("ai_review.input_bundle._file_sha256", return_value="2" * 64),
            mock.patch("ai_review.input_bundle._directory_sha256", return_value="3" * 64),
        ):
            out = Path(tmpdir) / "inputs"
            prepare_github_bundle(Path("ai-review/config/review.yaml"), out)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(client.fetch_pull_request.call_count, 3)
        client.fetch_pull_request.assert_has_calls(
            [mock.call("octo/repo", "32")] * 3
        )
        client.fetch_version.assert_not_called()
        client.fetch_diff.assert_not_called()
        client.fetch_comparison_diff.assert_called_once_with(
            "octo/repo", "0" * 40, "1" * 40
        )
        self.assertEqual(manifest["base_sha"], "0" * 40)
        self.assertEqual(manifest["head_sha"], "1" * 40)
        self.assertEqual(manifest["selected_head_sha"], "1" * 40)
        self.assertEqual(manifest["checkout_head_sha"], "1" * 40)
        self.assertEqual(
            manifest["diff_sha256"], sha256_hex("diff --git a/f.py b/f.py\n")
        )

    def test_manual_dispatch_requires_numeric_pull_request(self) -> None:
        with (
            mock.patch.dict(
                "os.environ", {"AI_REVIEW_GITHUB_PR_NUMBER": "not-a-number"}, clear=True
            ),
            self.assertRaisesRegex(SystemExit, "numeric AI_REVIEW_GITHUB_PR_NUMBER"),
        ):
            _resolve_github_pull_request(mock.Mock(), "octo/repo")

    def test_external_fork_pull_request_is_rejected(self) -> None:
        client = _github_platform_mock()
        client.fetch_pull_request.return_value = self._pull_request(
            number=32, source_repo="someone/fork"
        )
        with (
            mock.patch.dict("os.environ", {"AI_REVIEW_GITHUB_PR_NUMBER": "32"}, clear=True),
            self.assertRaisesRegex(SystemExit, "external fork PR secret-bearing path"),
        ):
            _resolve_github_pull_request(client, "octo/repo")

    def test_checkout_head_must_match_selected_sha(self) -> None:
        selected = "1" * 40
        with (
            mock.patch("ai_review.input_bundle._git_command", return_value="2" * 40),
            self.assertRaisesRegex(BundleError, "checkout HEAD differs"),
        ):
            _github_checkout_head(Path.cwd(), Path("inputs"), expected_head_sha=selected)

    def test_checkout_rejects_uncommitted_snapshot_input(self) -> None:
        selected = "1" * 40
        with (
            mock.patch(
                "ai_review.input_bundle._git_command",
                side_effect=[selected, "?? unexpected.txt"],
            ),
            self.assertRaisesRegex(BundleError, "uncommitted or ignored input"),
        ):
            _github_checkout_head(Path.cwd(), Path("inputs"), expected_head_sha=selected)

    def test_checkout_rejects_gitignored_snapshot_input(self) -> None:
        selected = "1" * 40
        with (
            mock.patch(
                "ai_review.input_bundle._git_command",
                side_effect=[selected, "", "generated.cache\0"],
            ),
            self.assertRaisesRegex(BundleError, "ignored input"),
        ):
            _github_checkout_head(Path.cwd(), Path("inputs"), expected_head_sha=selected)

    def test_checkout_outside_output_paths_skip_exclusion_pathspec(self) -> None:
        selected = "1" * 40
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            outside_paths = [Path("../relative-out"), Path(tmpdir) / "absolute-out"]
            for out_path in outside_paths:
                with self.subTest(out_path=out_path):
                    with mock.patch(
                        "ai_review.input_bundle._git_command",
                        side_effect=[selected, "", ""],
                    ) as git_command:
                        _github_checkout_head(repo, out_path, expected_head_sha=selected)
                    status_args = git_command.call_args_list[1].args[1:]
                    self.assertFalse(
                        any(arg.startswith(":(exclude)") for arg in status_args)
                    )

    @staticmethod
    def _init_git_repo(root: Path) -> str:
        root.mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        (root / ".gitignore").write_text("*.cache\ninputs/\n", encoding="utf-8")
        (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=root, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "-m",
                "initial",
            ],
            cwd=root,
            check=True,
        )
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_real_checkout_allows_only_the_inside_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            selected = self._init_git_repo(repo)
            out = repo / "inputs"
            out.mkdir()
            (out / "partial-artifact.json").write_text("{}\n", encoding="utf-8")

            self.assertEqual(
                _github_checkout_head(repo, out, expected_head_sha=selected), selected
            )

    def test_real_checkout_rejects_ignored_file_before_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            selected = self._init_git_repo(repo)
            (repo / "generated.cache").write_text("hidden\n", encoding="utf-8")
            out = Path(tmpdir) / "outside-inputs"

            with self.assertRaisesRegex(BundleError, r"ignored input.*generated\.cache"):
                _github_checkout_head(repo, out, expected_head_sha=selected)

    def _prepare_with_versions(
        self,
        tmpdir: str,
        versions: list[dict[str, object]],
        *,
        diff_error: Exception | None = None,
    ) -> tuple[mock.Mock, Path]:
        client = _github_platform_mock()
        client.fetch_pull_request.side_effect = versions
        if diff_error is None:
            client.fetch_comparison_diff.return_value = "diff --git a/f.py b/f.py\n"
        else:
            client.fetch_comparison_diff.side_effect = diff_error
        out = Path(tmpdir) / "inputs"
        patches = (
            mock.patch.dict(
                "os.environ",
                {
                    "GITHUB_REPOSITORY": "octo/repo",
                    "AI_REVIEW_GITHUB_PR_NUMBER": "7",
                    "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA": "1" * 40,
                    "GITHUB_RUN_ID": "100",
                    "GITHUB_RUN_ATTEMPT": "1",
                },
                clear=True,
            ),
            mock.patch("ai_review.input_bundle.load_config", return_value={}),
            mock.patch("ai_review.input_bundle.create_runtime_platform", return_value=client),
            mock.patch(
                "ai_review.input_bundle._github_checkout_head", return_value="1" * 40
            ),
            mock.patch(
                "ai_review.input_bundle._load_platform_state",
                side_effect=lambda _client, _config, state, **_kwargs: state,
            ),
            mock.patch("ai_review.input_bundle.shutil.copy2"),
            mock.patch("ai_review.input_bundle.shutil.copytree"),
            mock.patch("ai_review.input_bundle.copy_repo_snapshot"),
            mock.patch("ai_review.input_bundle._file_sha256", return_value="2" * 64),
            mock.patch("ai_review.input_bundle._directory_sha256", return_value="3" * 64),
        )
        with ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            prepare_github_bundle(Path("ai-review/config/review.yaml"), out)
        return client, out

    def test_prepare_rejects_platform_without_comparison_diff_support(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.dict(
                "os.environ",
                {
                    "GITHUB_REPOSITORY": "octo/repo",
                    "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA": "1" * 40,
                },
                clear=True,
            ),
            mock.patch("ai_review.input_bundle.load_config", return_value={}),
            mock.patch(
                "ai_review.input_bundle.create_runtime_platform",
                return_value=mock.Mock(spec=[]),
            ),
            mock.patch(
                "ai_review.input_bundle._github_checkout_head", return_value="1" * 40
            ),
            self.assertRaisesRegex(SystemExit, "comparison diff support"),
        ):
            prepare_github_bundle(
                Path("ai-review/config/review.yaml"), Path(tmpdir) / "inputs"
            )

    def test_prepare_uses_real_checkout_and_immutable_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            selected = self._init_git_repo(repo)
            pull_request = {
                "number": 7,
                "head": {
                    "ref": "feature",
                    "sha": selected,
                    "repo": {"full_name": "octo/repo"},
                },
                "base": {"ref": "main", "sha": "0" * 40},
            }
            client = _github_platform_mock()
            client.fetch_pull_request.return_value = pull_request
            client.fetch_comparison_diff.return_value = "diff --git a/f.py b/f.py\n"
            out = Path(tmpdir) / "outside-inputs"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "GITHUB_REPOSITORY": "octo/repo",
                        "AI_REVIEW_GITHUB_PR_NUMBER": "7",
                        "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA": selected,
                    },
                    clear=True,
                ),
                mock.patch("ai_review.input_bundle.Path.cwd", return_value=repo),
                mock.patch("ai_review.input_bundle.load_config", return_value={}),
                mock.patch(
                    "ai_review.input_bundle.create_runtime_platform", return_value=client
                ),
                mock.patch(
                    "ai_review.input_bundle._load_platform_state",
                    side_effect=lambda _client, _config, state, **_kwargs: state,
                ),
                mock.patch("ai_review.input_bundle.shutil.copy2"),
                mock.patch("ai_review.input_bundle.shutil.copytree"),
                mock.patch("ai_review.input_bundle.copy_repo_snapshot"),
                mock.patch("ai_review.input_bundle._file_sha256", return_value="2" * 64),
                mock.patch(
                    "ai_review.input_bundle._directory_sha256", return_value="3" * 64
                ),
            ):
                prepare_github_bundle(Path("ai-review/config/review.yaml"), out)

            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["selected_head_sha"], selected)
            self.assertEqual(manifest["checkout_head_sha"], selected)
            client.fetch_comparison_diff.assert_called_once_with(
                "octo/repo", "0" * 40, selected
            )

    def test_prepare_rejects_real_ignored_file_before_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            selected = self._init_git_repo(repo)
            (repo / "generated.cache").write_text("hidden\n", encoding="utf-8")
            out = Path(tmpdir) / "outside-inputs"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "GITHUB_REPOSITORY": "octo/repo",
                        "AI_REVIEW_GITHUB_PR_NUMBER": "7",
                        "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA": selected,
                    },
                    clear=True,
                ),
                mock.patch("ai_review.input_bundle.Path.cwd", return_value=repo),
                mock.patch("ai_review.input_bundle.copy_repo_snapshot") as snapshot,
                self.assertRaisesRegex(BundleError, "ignored input"),
            ):
                prepare_github_bundle(Path("ai-review/config/review.yaml"), out)

            snapshot.assert_not_called()
            self.assertFalse((out / "repo_snapshot").exists())

    def test_head_change_before_diff_fails_as_stale_input(self) -> None:
        changed = self._pull_request()
        changed["head"] = {
            "ref": "feature",
            "sha": "2" * 40,
            "repo": {"full_name": "octo/repo"},
        }
        with tempfile.TemporaryDirectory() as tmpdir, self.assertRaisesRegex(
            BundleError, "before diff collection"
        ):
            self._prepare_with_versions(tmpdir, [changed])

    def test_base_change_during_diff_collection_fails_before_diff_write(self) -> None:
        initial = self._pull_request()
        changed = self._pull_request()
        changed["base"] = {"ref": "main", "sha": "a" * 40}
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "inputs"
            with self.assertRaisesRegex(BundleError, "after diff collection"):
                self._prepare_with_versions(tmpdir, [initial, changed])
            self.assertFalse((out / "mr.diff").exists())
            self.assertFalse((out / "manifest.json").exists())

    def test_head_change_during_diff_collection_fails_before_diff_write(self) -> None:
        initial = self._pull_request()
        changed = self._pull_request()
        changed["head"] = {
            "ref": "feature",
            "sha": "2" * 40,
            "repo": {"full_name": "octo/repo"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "inputs"
            with self.assertRaisesRegex(BundleError, "after diff collection"):
                self._prepare_with_versions(tmpdir, [initial, changed])
            self.assertFalse((out / "mr.diff").exists())
            self.assertFalse((out / "manifest.json").exists())

    def test_head_change_at_manifest_finalization_fails(self) -> None:
        initial = self._pull_request()
        changed = self._pull_request()
        changed["head"] = {
            "ref": "feature",
            "sha": "2" * 40,
            "repo": {"full_name": "octo/repo"},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "inputs"
            with self.assertRaisesRegex(BundleError, "manifest finalization"):
                self._prepare_with_versions(tmpdir, [initial, initial, changed])
            self.assertFalse((out / "manifest.json").exists())

    def test_oversized_raw_diff_error_fails_closed(self) -> None:
        initial = self._pull_request()
        error = ReviewPlatformError(
            "GitHub rejected the raw diff as oversized "
            "(HTTP 406/too_large); no review bundle was produced"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "inputs"
            with self.assertRaisesRegex(BundleError, "oversized"):
                self._prepare_with_versions(tmpdir, [initial], diff_error=error)
            self.assertFalse((out / "mr.diff").exists())
            self.assertFalse((out / "manifest.json").exists())


class RepoSnapshotContainmentTests(unittest.TestCase):
    def _write_nested_repo(self, root: Path) -> None:
        (root / "src" / "pkg").mkdir(parents=True)
        (root / "src" / "pkg" / "mod.py").write_text("value = 1\n", encoding="utf-8")
        (root / "README.md").write_text("# demo\n", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("gitdir\n", encoding="utf-8")
        (root / ".ai-review-local").mkdir()
        (root / ".ai-review-local" / "cache").write_text("cache\n", encoding="utf-8")

    def test_regular_nested_files_copy_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "out" / "repo_snapshot"
            self._write_nested_repo(source)
            copy_repo_snapshot(source, dest, ignore_top_level_names={"out"})

            self.assertEqual(
                (dest / "src" / "pkg" / "mod.py").read_text(encoding="utf-8"),
                "value = 1\n",
            )
            self.assertEqual((dest / "README.md").read_text(encoding="utf-8"), "# demo\n")
            self.assertFalse((dest / ".git").exists())
            self.assertFalse((dest / ".ai-review-local").exists())

    def test_nested_dir_sharing_output_basename_is_not_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "inputs" / "repo_snapshot"
            self._write_nested_repo(source)
            nested = source / "pkg" / "inputs"
            nested.mkdir(parents=True)
            (nested / "kept.py").write_text("kept = True\n", encoding="utf-8")
            copy_repo_snapshot(source, dest, ignore_top_level_names={"inputs"})
            self.assertTrue((dest / "pkg" / "inputs" / "kept.py").is_file())
            self.assertEqual(
                (dest / "pkg" / "inputs" / "kept.py").read_text(encoding="utf-8"),
                "kept = True\n",
            )

    def test_top_level_output_dir_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            self._write_nested_repo(source)
            out_dir = source / "inputs"
            out_dir.mkdir()
            (out_dir / "secret.bin").write_text("nope\n", encoding="utf-8")
            dest = out_dir / "repo_snapshot"
            copy_repo_snapshot(source, dest, ignore_top_level_names={"inputs"})
            self.assertFalse((dest / "inputs").exists())
            self.assertTrue((dest / "README.md").is_file())

    def test_mode_bits_strip_setuid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            target = source / "src" / "pkg" / "mod.py"
            target.chmod(0o4755)
            copy_repo_snapshot(source, dest)
            mode = (dest / "src" / "pkg" / "mod.py").stat().st_mode
            self.assertEqual(stat.S_IMODE(mode), 0o755)
            self.assertFalse(mode & stat.S_ISUID)

    def test_published_snapshot_directory_mode_is_0755(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            copy_repo_snapshot(source, dest)
            self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o755)

    def test_snapshot_is_deterministic_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            self._write_nested_repo(source)
            first = Path(tmpdir) / "snap-a"
            second = Path(tmpdir) / "snap-b"
            copy_repo_snapshot(source, first)
            copy_repo_snapshot(source, second)
            first_files = {
                path.relative_to(first).as_posix(): path.read_bytes()
                for path in sorted(first.rglob("*"))
                if path.is_file()
            }
            second_files = {
                path.relative_to(second).as_posix(): path.read_bytes()
                for path in sorted(second.rglob("*"))
                if path.is_file()
            }
            self.assertEqual(first_files, second_files)

    def _assert_symlink_rejected(self, link_rel: str, target: str | Path) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            link_path = source / link_rel
            link_path.parent.mkdir(parents=True, exist_ok=True)
            link_path.symlink_to(target)
            with self.assertRaisesRegex(BundleError, rf"rejects symlink: {link_rel}"):
                copy_repo_snapshot(source, dest)
            self.assertFalse(dest.exists())

    def test_relative_file_symlink_is_rejected(self) -> None:
        self._assert_symlink_rejected("src/alias.py", "pkg/mod.py")

    def test_absolute_file_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            outside = Path(tmpdir) / "secret.txt"
            outside.write_text("secret\n", encoding="utf-8")
            self._assert_symlink_rejected("leak.txt", outside)

    def test_parent_escaping_symlink_is_rejected(self) -> None:
        self._assert_symlink_rejected("escape", "../outside")

    def test_dangling_symlink_is_rejected(self) -> None:
        self._assert_symlink_rejected("missing", "does-not-exist")

    def test_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            (source / "real_dir").mkdir()
            (source / "link_dir").symlink_to("real_dir")
            with self.assertRaisesRegex(BundleError, r"rejects symlink: link_dir"):
                copy_repo_snapshot(source, dest)
            self.assertFalse(dest.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "proc environ path is Linux-only")
    def test_proc_environ_symlink_never_materializes_sentinel(self) -> None:
        sentinel = "AI_REVIEW_SNAPSHOT_SENTINEL=leaked-token-value-9f3c"
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            (source / "environ.link").symlink_to("/proc/self/environ")
            with (
                mock.patch.dict("os.environ", {"AI_REVIEW_SNAPSHOT_SENTINEL": sentinel}),
                self.assertRaisesRegex(BundleError, r"rejects symlink: environ.link"),
            ):
                copy_repo_snapshot(source, dest)
            self.assertFalse(dest.exists())
            leaked = list(Path(tmpdir).rglob("*"))
            for path in leaked:
                if not path.is_file() or path.is_symlink():
                    continue
                self.assertNotIn(sentinel.encode("utf-8"), path.read_bytes())

    def test_fifo_is_rejected_promptly(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("os.mkfifo is unavailable on this platform")
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            fifo_path = source / "pipe.fifo"
            os.mkfifo(fifo_path)
            self.assertTrue(stat.S_ISFIFO(os.lstat(fifo_path).st_mode))
            with self.assertRaisesRegex(BundleError, r"rejects special file: pipe.fifo"):
                copy_repo_snapshot(source, dest)
            self.assertFalse(dest.exists())

    def test_symlink_mode_skip_omits_relative_file_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            (source / "src" / "alias.py").symlink_to("pkg/mod.py")
            copy_repo_snapshot(source, dest, symlink_mode="skip")
            # Regular files still copy; the symlink is absent from the snapshot.
            self.assertEqual(
                (dest / "src" / "pkg" / "mod.py").read_text(encoding="utf-8"),
                "value = 1\n",
            )
            self.assertFalse((dest / "src" / "alias.py").exists())
            self.assertFalse((dest / "src" / "alias.py").is_symlink())

    def test_symlink_mode_skip_omits_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            (source / "real_dir").mkdir()
            (source / "real_dir" / "kept.txt").write_text("kept\n", encoding="utf-8")
            (source / "link_dir").symlink_to("real_dir")
            copy_repo_snapshot(source, dest, symlink_mode="skip")
            # The real directory is copied; the directory symlink is not descended.
            self.assertTrue((dest / "real_dir" / "kept.txt").is_file())
            self.assertFalse((dest / "link_dir").exists())
            self.assertFalse((dest / "link_dir").is_symlink())

    @unittest.skipUnless(sys.platform.startswith("linux"), "proc environ path is Linux-only")
    def test_symlink_mode_skip_never_materializes_proc_environ(self) -> None:
        sentinel = "AI_REVIEW_SNAPSHOT_SENTINEL=leaked-token-value-9f3c"
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            (source / "environ.link").symlink_to("/proc/self/environ")
            with mock.patch.dict(
                "os.environ", {"AI_REVIEW_SNAPSHOT_SENTINEL": sentinel}
            ):
                copy_repo_snapshot(source, dest, symlink_mode="skip")
            # Skip omits the link entirely — it is never followed, so no target
            # content (and no sentinel) reaches the published snapshot.
            self.assertFalse((dest / "environ.link").exists())
            self.assertTrue((dest / "README.md").is_file())
            for path in Path(tmpdir).rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                self.assertNotIn(sentinel.encode("utf-8"), path.read_bytes())

    def test_symlink_mode_skip_reports_omitted_symlinks_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            (source / "src" / "alias.py").symlink_to("pkg/mod.py")
            (source / "top.link").symlink_to("README.md")
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                copy_repo_snapshot(source, dest, symlink_mode="skip")
            stderr = captured.getvalue()
            # Each omitted symlink is named, and a summary count is emitted.
            self.assertIn("snapshot skipped symlink: src/alias.py", stderr)
            self.assertIn("snapshot skipped symlink: top.link", stderr)
            self.assertIn("omitted 2 symlink(s)", stderr)

    def test_symlink_mode_reject_emits_no_skip_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                copy_repo_snapshot(source, dest)
            self.assertNotIn("snapshot skipped symlink", captured.getvalue())

    def test_symlink_mode_skip_still_rejects_special_files(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("os.mkfifo is unavailable on this platform")
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            os.mkfifo(source / "pipe.fifo")
            # Skip is symlink-only; special files still fail closed.
            with self.assertRaisesRegex(BundleError, r"rejects special file: pipe.fifo"):
                copy_repo_snapshot(source, dest, symlink_mode="skip")
            self.assertFalse(dest.exists())

    def test_skip_diagnostic_escapes_control_characters_in_names(self) -> None:
        # A crafted filename containing a newline + a forged CI workflow command
        # must not produce a standalone "::error::" line in stderr.
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            hostile = "evil\n::error::forged\x1b[31m"
            try:
                (source / hostile).symlink_to("README.md")
            except (OSError, ValueError):
                self.skipTest("filesystem rejects control characters in names")
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                copy_repo_snapshot(source, dest, symlink_mode="skip")
            stderr = captured.getvalue()
            self.assertNotIn("\n::error::forged", stderr)
            self.assertNotIn("\x1b", stderr)
            self.assertIn("\\x0a", stderr)  # newline rendered as an escape
            # Every emitted line still carries the diagnostic prefix.
            for line in stderr.splitlines():
                self.assertTrue(line.startswith("ai-review: "), line)

    def test_skip_diagnostic_escapes_unicode_bidi_controls(self) -> None:
        # Bidi/format controls (Cf) can visually spoof a log line; escape them too.
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            hostile = "safe‮dnegol-daor​.txt"  # RLO + zero-width space
            try:
                (source / hostile).symlink_to("README.md")
            except (OSError, ValueError):
                self.skipTest("filesystem rejects these characters in names")
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                copy_repo_snapshot(source, dest, symlink_mode="skip")
            stderr = captured.getvalue()
            self.assertNotIn("‮", stderr)
            self.assertNotIn("​", stderr)
            self.assertIn("\\u202e", stderr)
            self.assertIn("\\u200b", stderr)

    def test_skip_report_records_count_and_bounded_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            for i in range(25):
                (source / f"link{i:02d}").symlink_to("README.md")
            report: dict[str, object] = {}
            copy_repo_snapshot(
                source, dest, symlink_mode="skip", skipped_report=report
            )
            self.assertEqual(report["count"], 25)
            self.assertEqual(len(report["sample"]), 20)  # bounded

    def test_skip_report_empty_when_no_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            report: dict[str, object] = {}
            copy_repo_snapshot(
                source, dest, symlink_mode="skip", skipped_report=report
            )
            self.assertEqual(report["count"], 0)
            self.assertEqual(report["sample"], [])

    def test_skip_diagnostic_is_bounded_for_many_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            for i in range(50):
                (source / f"link{i:02d}").symlink_to("README.md")
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                copy_repo_snapshot(source, dest, symlink_mode="skip")
            stderr = captured.getvalue()
            per_path = [
                ln for ln in stderr.splitlines()
                if "snapshot skipped symlink:" in ln
            ]
            self.assertEqual(len(per_path), 20)  # capped sample
            self.assertIn("omitted 50 symlink(s)", stderr)
            self.assertIn("showing first 20, 30 more", stderr)

    def test_invalid_symlink_mode_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            for bad in ("follow", ["skip"], True):
                with self.assertRaises(ValueError):
                    copy_repo_snapshot(source, dest, symlink_mode=bad)
            self.assertFalse(dest.exists())

    def test_symlink_mode_skip_still_fails_closed_on_copy_race(self) -> None:
        # The "skip" relaxation only applies to entries that are symlinks at scan
        # time; a file swapped to a symlink mid-copy must still fail closed.
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            victim = (source / "src" / "pkg" / "mod.py").resolve()
            outside = Path(tmpdir) / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")

            def race_then_copy(
                dst: Path,
                expected: os.stat_result,
                rel_parts: tuple[str, ...],
                *,
                dir_fd: int,
                name: str,
            ) -> None:
                if rel_parts == ("src", "pkg", "mod.py"):
                    victim.unlink()
                    victim.symlink_to(outside)
                _copy_regular_file_nofollow(
                    dst, expected, rel_parts, dir_fd=dir_fd, name=name
                )

            with (
                mock.patch(
                    "ai_review.input_bundle._copy_regular_file_nofollow",
                    side_effect=race_then_copy,
                ),
                self.assertRaisesRegex(BundleError, r"rejects symlink: src/pkg/mod.py"),
            ):
                copy_repo_snapshot(source, dest, symlink_mode="skip")
            self.assertFalse(dest.exists())

    def test_validation_copy_race_replacing_file_with_symlink_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            self._write_nested_repo(source)
            victim = (source / "src" / "pkg" / "mod.py").resolve()
            outside = Path(tmpdir) / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")

            def race_then_copy(
                dst: Path,
                expected: os.stat_result,
                rel_parts: tuple[str, ...],
                *,
                dir_fd: int,
                name: str,
            ) -> None:
                if rel_parts == ("src", "pkg", "mod.py"):
                    victim.unlink()
                    victim.symlink_to(outside)
                _copy_regular_file_nofollow(
                    dst, expected, rel_parts, dir_fd=dir_fd, name=name
                )

            with (
                mock.patch(
                    "ai_review.input_bundle._copy_regular_file_nofollow",
                    side_effect=race_then_copy,
                ),
                self.assertRaisesRegex(BundleError, r"rejects symlink: src/pkg/mod.py"),
            ):
                copy_repo_snapshot(source, dest)
            self.assertFalse(dest.exists())
            self.assertNotIn(b"outside", b"".join(
                path.read_bytes()
                for path in Path(tmpdir).rglob("*")
                if path.is_file() and not path.is_symlink() and path != outside
            ))

    def test_directory_symlink_swap_during_descent_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            outside = Path(tmpdir) / "outside"
            outside.mkdir()
            (outside / "TOP-SECRET").write_text("classified\n", encoding="utf-8")
            self._write_nested_repo(source)
            nested = source / "nested"
            nested.mkdir()
            (nested / "inner.txt").write_text("inner\n", encoding="utf-8")
            original_open = os.open

            def racing_open(
                path: str | bytes | os.PathLike[str],
                flags: int,
                *args: object,
                **kwargs: object,
            ) -> int:
                # After the directory was validated, swap it for an escaping symlink
                # before the O_NOFOLLOW|O_DIRECTORY open (dir_fd path) runs.
                if (
                    kwargs.get("dir_fd") is not None
                    and path == "nested"
                    and nested.exists()
                    and not nested.is_symlink()
                ):
                    shutil.rmtree(nested)
                    nested.symlink_to(outside)
                return original_open(path, flags, *args, **kwargs)

            with (
                mock.patch("ai_review.input_bundle.os.open", side_effect=racing_open),
                # Linux may surface ELOOP ("symlink") or ENOTDIR when O_DIRECTORY|
                # O_NOFOLLOW hits a swapped symlink; both are fail-closed.
                self.assertRaisesRegex(
                    BundleError, r"rejects (symlink|non-directory): nested"
                ),
            ):
                copy_repo_snapshot(source, dest)
            self.assertFalse(dest.exists())

    def test_missing_dir_fd_support_fails_closed_without_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            dest = Path(tmpdir) / "repo_snapshot"
            outside = Path(tmpdir) / "outside"
            outside.mkdir()
            (outside / "TOP-SECRET").write_text("classified\n", encoding="utf-8")
            self._write_nested_repo(source)
            nested = source / "nested"
            nested.mkdir()
            (nested / "inner.txt").write_text("inner\n", encoding="utf-8")
            # Simulate a hostile swap that the removed path fallback could follow.
            shutil.rmtree(nested)
            nested.symlink_to(outside)

            with (
                mock.patch("ai_review.input_bundle._DIR_FD_SUPPORTED", False),
                self.assertRaisesRegex(
                    BundleError, r"requires platform support for dir_fd-relative"
                ),
            ):
                copy_repo_snapshot(source, dest)
            self.assertFalse(dest.exists())
            leaked = [
                path
                for path in Path(tmpdir).rglob("TOP-SECRET")
                if "outside" not in path.parts
            ]
            self.assertEqual(leaked, [])

    def test_destination_inside_source_requires_ignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            self._write_nested_repo(source)
            dest = source / "nested-out" / "repo_snapshot"
            with self.assertRaisesRegex(BundleError, r"not ignored: nested-out"):
                copy_repo_snapshot(source, dest)

    def test_local_prepare_uses_shared_snapshot_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            self._write_nested_repo(repo)
            out = Path(tmpdir) / "bundle"
            diff = Path(tmpdir) / "mr.diff"
            diff.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
            with mock.patch(
                "ai_review.input_bundle.copy_repo_snapshot",
                wraps=copy_repo_snapshot,
            ) as mocked:
                prepare_local_bundle(_REPO_CONFIG, diff, repo, out)
            mocked.assert_called_once()
            self.assertTrue((out / "repo_snapshot" / "README.md").is_file())

    def test_github_and_gitlab_prepare_use_shared_snapshot_builder(self) -> None:
        class GitLabClient:
            def fetch_version(self, project_id: str, change_id: str) -> object:
                return type("V", (), {"base_sha": "b", "start_sha": "s", "head_sha": "h"})()

            def fetch_diff(self, project_id: str, change_id: str) -> str:
                return "diff --git a/f.py b/f.py\n"

        github_client = _github_platform_mock()
        github_client.fetch_pull_request.return_value = {
            "number": 9,
            "head": {
                "ref": "feature",
                "sha": "1" * 40,
                "repo": {"full_name": "octo/repo"},
            },
            "base": {"ref": "main", "sha": "0" * 40},
        }
        github_client.fetch_comparison_diff.return_value = "diff --git a/f.py b/f.py\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "inputs"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "GITHUB_REPOSITORY": "octo/repo",
                        "AI_REVIEW_GITHUB_PR_NUMBER": "9",
                        "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA": "1" * 40,
                        "GITHUB_RUN_ID": "1",
                        "GITHUB_RUN_ATTEMPT": "1",
                    },
                    clear=True,
                ),
                mock.patch("ai_review.input_bundle.load_config", return_value={}),
                mock.patch(
                    "ai_review.input_bundle.create_runtime_platform",
                    return_value=github_client,
                ),
                mock.patch(
                    "ai_review.input_bundle._github_checkout_head", return_value="1" * 40
                ),
                mock.patch(
                    "ai_review.input_bundle._load_platform_state",
                    side_effect=lambda _client, _config, state, **_kwargs: state,
                ),
                mock.patch("ai_review.input_bundle.shutil.copy2"),
                mock.patch("ai_review.input_bundle.shutil.copytree"),
                mock.patch("ai_review.input_bundle.copy_repo_snapshot") as snap,
                mock.patch("ai_review.input_bundle._file_sha256", return_value="a" * 64),
                mock.patch("ai_review.input_bundle._directory_sha256", return_value="b" * 64),
            ):
                prepare_github_bundle(Path("ai-review/config/review.yaml"), out)
            snap.assert_called_once()
            self.assertEqual(snap.call_args.args[0], Path.cwd())
            self.assertEqual(snap.call_args.args[1], out / "repo_snapshot")
            self.assertEqual(snap.call_args.kwargs["symlink_mode"], "reject")

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "inputs"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "CI_API_V4_URL": "https://gitlab.example/api/v4",
                        "CI_PROJECT_ID": "1",
                        "CI_MERGE_REQUEST_IID": "2",
                        "GITLAB_TOKEN": "token",
                    },
                    clear=True,
                ),
                mock.patch(
                    "ai_review.input_bundle.load_config",
                    return_value={"state": {"backend": "none"}},
                ),
                mock.patch(
                    "ai_review.input_bundle.create_runtime_platform",
                    return_value=GitLabClient(),
                ),
                mock.patch("ai_review.input_bundle.shutil.copy2"),
                mock.patch("ai_review.input_bundle.shutil.copytree"),
                mock.patch("ai_review.input_bundle.copy_repo_snapshot") as snap,
                mock.patch("ai_review.input_bundle._file_sha256", return_value="c" * 64),
                mock.patch("ai_review.input_bundle._directory_sha256", return_value="d" * 64),
            ):
                prepare_gitlab_bundle(Path("ai-review/config/review.yaml"), out)
            snap.assert_called_once()
            self.assertEqual(snap.call_args.args[0], Path.cwd())
            self.assertEqual(snap.call_args.args[1], out / "repo_snapshot")
            self.assertEqual(snap.call_args.kwargs["symlink_mode"], "reject")

    def test_prepare_threads_skip_symlink_mode_from_config(self) -> None:
        skip_cfg = {"security": {"snapshot_symlink_mode": "skip"}}

        # Local prepare.
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            self._write_nested_repo(repo)
            out = Path(tmpdir) / "bundle"
            diff = Path(tmpdir) / "mr.diff"
            diff.write_text("diff --git a/a.py b/a.py\n", encoding="utf-8")
            with (
                mock.patch("ai_review.input_bundle.load_config", return_value=skip_cfg),
                mock.patch("ai_review.input_bundle.shutil.copy2"),
                mock.patch("ai_review.input_bundle.shutil.copytree"),
                mock.patch("ai_review.input_bundle.copy_repo_snapshot") as snap,
                mock.patch("ai_review.input_bundle._file_sha256", return_value="a" * 64),
                mock.patch("ai_review.input_bundle._directory_sha256", return_value="b" * 64),
            ):
                prepare_local_bundle(Path("cfg/review.yaml"), diff, repo, out)
            self.assertEqual(snap.call_args.kwargs["symlink_mode"], "skip")

        # GitHub prepare.
        github_client = _github_platform_mock()
        github_client.fetch_pull_request.return_value = {
            "number": 9,
            "head": {"ref": "feature", "sha": "1" * 40, "repo": {"full_name": "octo/repo"}},
            "base": {"ref": "main", "sha": "0" * 40},
        }
        github_client.fetch_comparison_diff.return_value = "diff --git a/f.py b/f.py\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "inputs"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "GITHUB_REPOSITORY": "octo/repo",
                        "AI_REVIEW_GITHUB_PR_NUMBER": "9",
                        "AI_REVIEW_GITHUB_EXPECTED_HEAD_SHA": "1" * 40,
                        "GITHUB_RUN_ID": "1",
                        "GITHUB_RUN_ATTEMPT": "1",
                    },
                    clear=True,
                ),
                mock.patch("ai_review.input_bundle.load_config", return_value=skip_cfg),
                mock.patch(
                    "ai_review.input_bundle.create_runtime_platform",
                    return_value=github_client,
                ),
                mock.patch(
                    "ai_review.input_bundle._github_checkout_head", return_value="1" * 40
                ),
                mock.patch(
                    "ai_review.input_bundle._load_platform_state",
                    side_effect=lambda _client, _config, state, **_kwargs: state,
                ),
                mock.patch("ai_review.input_bundle.shutil.copy2"),
                mock.patch("ai_review.input_bundle.shutil.copytree"),
                mock.patch("ai_review.input_bundle.copy_repo_snapshot") as snap,
                mock.patch("ai_review.input_bundle._file_sha256", return_value="a" * 64),
                mock.patch("ai_review.input_bundle._directory_sha256", return_value="b" * 64),
            ):
                prepare_github_bundle(Path("ai-review/config/review.yaml"), out)
            self.assertEqual(snap.call_args.kwargs["symlink_mode"], "skip")

        # GitLab prepare.
        class GitLabClient:
            def fetch_version(self, project_id: str, change_id: str) -> object:
                return type("V", (), {"base_sha": "b", "start_sha": "s", "head_sha": "h"})()

            def fetch_diff(self, project_id: str, change_id: str) -> str:
                return "diff --git a/f.py b/f.py\n"

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "inputs"
            with (
                mock.patch.dict(
                    "os.environ",
                    {
                        "CI_API_V4_URL": "https://gitlab.example/api/v4",
                        "CI_PROJECT_ID": "1",
                        "CI_MERGE_REQUEST_IID": "2",
                        "GITLAB_TOKEN": "token",
                    },
                    clear=True,
                ),
                mock.patch(
                    "ai_review.input_bundle.load_config",
                    return_value={
                        "security": {"snapshot_symlink_mode": "skip"},
                        "state": {"backend": "none"},
                    },
                ),
                mock.patch(
                    "ai_review.input_bundle.create_runtime_platform",
                    return_value=GitLabClient(),
                ),
                mock.patch("ai_review.input_bundle.shutil.copy2"),
                mock.patch("ai_review.input_bundle.shutil.copytree"),
                mock.patch("ai_review.input_bundle.copy_repo_snapshot") as snap,
                mock.patch("ai_review.input_bundle._file_sha256", return_value="c" * 64),
                mock.patch("ai_review.input_bundle._directory_sha256", return_value="d" * 64),
            ):
                prepare_gitlab_bundle(Path("ai-review/config/review.yaml"), out)
            self.assertEqual(snap.call_args.kwargs["symlink_mode"], "skip")

    def test_symlinks_touched_by_diff_flags_file_and_directory_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "repo"
            self._write_nested_repo(source)
            (source / "alias.py").symlink_to("README.md")
            (source / "linkdir").symlink_to("src")
            diff = (
                "diff --git a/alias.py b/alias.py\n"
                "diff --git a/linkdir/mod.py b/linkdir/mod.py\n"
                "diff --git a/README.md b/README.md\n"
            )
            touched = _symlinks_touched_by_diff(source, diff)
            # The file symlink and the path reached through the dir symlink are
            # flagged; the ordinary changed file is not.
            self.assertEqual(touched, ["alias.py", "linkdir"])

    def test_local_prepare_records_skipped_symlinks_and_warns_on_diff(self) -> None:
        skip_cfg = {"security": {"snapshot_symlink_mode": "skip"}}
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            self._write_nested_repo(repo)
            (repo / "alias.py").symlink_to("README.md")
            out = Path(tmpdir) / "bundle"
            diff = Path(tmpdir) / "mr.diff"
            diff.write_text("diff --git a/alias.py b/alias.py\n", encoding="utf-8")
            captured = io.StringIO()
            with (
                mock.patch("ai_review.input_bundle.load_config", return_value=skip_cfg),
                mock.patch("ai_review.input_bundle.shutil.copy2"),
                mock.patch("ai_review.input_bundle.shutil.copytree"),
                mock.patch("ai_review.input_bundle._file_sha256", return_value="a" * 64),
                mock.patch(
                    "ai_review.input_bundle._directory_sha256", return_value="b" * 64
                ),
                contextlib.redirect_stderr(captured),
            ):
                prepare_local_bundle(Path("cfg/review.yaml"), diff, repo, out)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["snapshot_skipped_symlink_count"], 1)
            self.assertIn("alias.py", manifest["snapshot_skipped_symlink_sample"])
            # The MR changed the omitted symlink → elevated warning.
            self.assertIn("omitted from repo_snapshot", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
