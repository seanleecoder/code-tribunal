from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from ai_review.platform.runtime import PlatformRuntimeError, create_runtime_platform


class PlatformRuntimeTests(unittest.TestCase):
    def test_github_mode_uses_github_factory(self) -> None:
        config = {"posting": {"mode": "github_reviews"}}
        platform = object()
        with mock.patch(
            "ai_review.platform.runtime.create_github_platform", return_value=platform
        ) as factory:
            self.assertIs(
                create_runtime_platform(
                    config,
                    access="write",
                    env={"GITHUB_API_URL": "https://github.example/api", "GITHUB_TOKEN": "x"},
                ),
                platform,
            )
        factory.assert_called_once_with("https://github.example/api", "x")

    def test_gitlab_mode_selects_access_specific_token(self) -> None:
        config = {"posting": {"mode": "gitlab_discussions"}}
        with mock.patch("ai_review.platform.runtime.create_gitlab_platform") as factory:
            create_runtime_platform(
                config,
                access="read",
                env={
                    "CI_API_V4_URL": "https://gitlab.example/api/v4",
                    "GITLAB_READ_TOKEN": "r",
                },
            )
            create_runtime_platform(
                config,
                access="write",
                env={
                    "CI_API_V4_URL": "https://gitlab.example/api/v4",
                    "GITLAB_WRITE_TOKEN": "w",
                },
            )
        self.assertEqual(
            factory.call_args_list[0].args, ("https://gitlab.example/api/v4", "r")
        )
        self.assertEqual(
            factory.call_args_list[1].args, ("https://gitlab.example/api/v4", "w")
        )

    def test_missing_secret_fails_before_platform_io(self) -> None:
        with self.assertRaisesRegex(PlatformRuntimeError, "GITHUB_TOKEN"):
            create_runtime_platform(
                {"posting": {"mode": "github_reviews"}}, access="read", env={}
            )

    def test_cli_modules_do_not_select_concrete_factories(self) -> None:
        source_root = Path(__file__).resolve().parents[2] / "src" / "ai_review"
        for module_name in ("post.py", "input_bundle.py"):
            with self.subTest(module=module_name):
                source = (source_root / module_name).read_text(encoding="utf-8")
                self.assertNotIn("platform.factory", source)
                self.assertNotIn("create_gitlab_platform", source)
                self.assertNotIn("create_github_platform", source)
                self.assertIn("create_runtime_platform", source)


if __name__ == "__main__":
    unittest.main()
