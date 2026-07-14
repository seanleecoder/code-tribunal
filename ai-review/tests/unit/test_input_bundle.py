from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_review.gitlab_client import MergeRequestVersion
from ai_review.input_bundle import (
    BundleError,
    _enforce_diff_limits,
    _external_fork_secrets_blocked,
    _resolve_github_pull_request,
    prepare_gitlab_bundle,
)


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
                    "GITLAB_READ_TOKEN": "token",
                },
                clear=True,
            ),
            mock.patch(
                "ai_review.input_bundle.load_config",
                return_value={
                    "state": {
                        "backend": "gitlab_mr_state_note",
                        "overflow_behavior": "fail_closed",
                    }
                },
            ),
            mock.patch(
                "ai_review.input_bundle.create_runtime_platform",
                return_value=BrokenUserClient(),
            ),
            mock.patch("ai_review.input_bundle.shutil.copy2"),
            mock.patch("ai_review.input_bundle.shutil.copytree"),
            mock.patch("ai_review.input_bundle._file_sha256", return_value="0" * 64),
            mock.patch("ai_review.input_bundle._directory_sha256", return_value="1" * 64),
            self.assertRaisesRegex(BundleError, "current_user"),
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

    def test_resolves_pull_request_event_without_api_metadata_fetch(self) -> None:
        client = mock.Mock()
        with tempfile.TemporaryDirectory() as tmpdir:
            event_path = Path(tmpdir) / "event.json"
            expected = self._pull_request()
            event_path.write_text(json.dumps({"pull_request": expected}), encoding="utf-8")
            with mock.patch.dict(
                "os.environ", {"GITHUB_EVENT_PATH": str(event_path)}, clear=True
            ):
                actual = _resolve_github_pull_request(client, "octo/repo")

        self.assertEqual(actual, expected)
        client.fetch_pull_request.assert_not_called()

    def test_manual_dispatch_fetches_requested_pull_request(self) -> None:
        client = mock.Mock()
        expected = self._pull_request(number=32)
        client.fetch_pull_request.return_value = expected
        with mock.patch.dict(
            "os.environ", {"AI_REVIEW_GITHUB_PR_NUMBER": "32"}, clear=True
        ):
            actual = _resolve_github_pull_request(client, "octo/repo")

        self.assertEqual(actual, expected)
        client.fetch_pull_request.assert_called_once_with("octo/repo", "32")

    def test_manual_dispatch_requires_numeric_pull_request(self) -> None:
        with (
            mock.patch.dict(
                "os.environ", {"AI_REVIEW_GITHUB_PR_NUMBER": "not-a-number"}, clear=True
            ),
            self.assertRaisesRegex(SystemExit, "numeric AI_REVIEW_GITHUB_PR_NUMBER"),
        ):
            _resolve_github_pull_request(mock.Mock(), "octo/repo")

    def test_external_fork_pull_request_is_rejected(self) -> None:
        client = mock.Mock()
        client.fetch_pull_request.return_value = self._pull_request(
            number=32, source_repo="someone/fork"
        )
        with (
            mock.patch.dict(
                "os.environ", {"AI_REVIEW_GITHUB_PR_NUMBER": "32"}, clear=True
            ),
            self.assertRaisesRegex(SystemExit, "external fork PR secret-bearing path"),
        ):
            _resolve_github_pull_request(client, "octo/repo")


if __name__ == "__main__":
    unittest.main()
