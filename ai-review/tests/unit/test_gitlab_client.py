from __future__ import annotations

import contextlib
import io
import unittest
from typing import Any
from unittest import mock

from ai_review.gitlab_client import (
    GitLabApiError,
    GitLabClient,
    MergeRequestVersion,
    build_position,
    root_note_id_from_discussion,
)


class FakeResponse:
    def __init__(
        self, payload: Any, status_code: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = "x"
        self.headers = headers or {}

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if url.endswith("/versions"):
            return FakeResponse(
                [
                    {
                        "id": 1,
                        "base_commit_sha": "base-old",
                        "start_commit_sha": "start-old",
                        "head_commit_sha": "head-old",
                    },
                    {
                        "id": 2,
                        "base_commit_sha": "base",
                        "start_commit_sha": "start",
                        "head_commit_sha": "head",
                    },
                ]
            )
        if url.endswith("/notes"):
            return FakeResponse([])
        if "/merge_requests/" in url and method == "GET":
            return FakeResponse({"sha": "head"})
        return FakeResponse({"id": "discussion", "notes": [{"id": 123}]})


class DiffFallbackSession:
    def __init__(self, primary: list[dict[str, Any]], raw_payload: Any) -> None:
        self.primary = primary
        self.raw_payload = raw_payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if url.endswith("/changes"):
            return FakeResponse(self.raw_payload)
        return FakeResponse(self.primary, headers={"X-Next-Page": ""})


class GitLabClientTests(unittest.TestCase):
    def _anchor(self, side: str) -> dict[str, Any]:
        start = {
            "old_line": 10 if side in {"old", "unchanged"} else None,
            "new_line": 12 if side in {"new", "unchanged"} else None,
            "line_code": "line-start",
        }
        end = {
            "old_line": 11 if side in {"old", "unchanged"} else None,
            "new_line": 13 if side in {"new", "unchanged"} else None,
            "line_code": "line-end",
        }
        return {
            "old_path": "src/foo.py",
            "new_path": "src/foo.py",
            "side": side,
            "start": start,
            "end": end,
        }

    def test_build_position_line_rules(self) -> None:
        version = MergeRequestVersion("base", "start", "head")
        self.assertEqual(build_position(self._anchor("new"), version)["new_line"], 12)
        self.assertNotIn("old_line", build_position(self._anchor("new"), version))
        self.assertEqual(build_position(self._anchor("old"), version)["old_line"], 10)
        self.assertNotIn("new_line", build_position(self._anchor("old"), version))
        unchanged = build_position(self._anchor("unchanged"), version)
        self.assertEqual(unchanged["old_line"], 10)
        self.assertEqual(unchanged["new_line"], 12)

    def test_build_position_multiline_adds_line_range(self) -> None:
        position = build_position(
            self._anchor("new"), MergeRequestVersion("b", "s", "h"), multiline=True
        )
        self.assertEqual(position["line_range"]["start"]["line_code"], "line-start")
        self.assertEqual(position["line_range"]["end"]["line_code"], "line-end")

    def test_fetch_latest_version_uses_highest_id(self) -> None:
        session = FakeSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        version = client.fetch_latest_mr_version("group/project", 1)
        self.assertEqual(version, MergeRequestVersion("base", "start", "head"))
        method, url, kwargs = session.calls[0]
        self.assertEqual(method, "GET")
        self.assertIn("group%2Fproject", url)
        self.assertEqual(kwargs["headers"]["PRIVATE-TOKEN"], "token")

    def test_fetch_current_mr_head_sha(self) -> None:
        session = FakeSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        self.assertEqual(client.fetch_current_mr_head_sha("group/project", 1), "head")

    def test_root_note_id_from_discussion(self) -> None:
        self.assertEqual(root_note_id_from_discussion({"notes": [{"id": 123}]}), 123)

    def test_gitlab_state_and_resolution_methods(self) -> None:
        session = FakeSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        client.list_mr_notes("group/project", 1)
        client.resolve_discussion("group/project", 1, "discussion", False)
        client.current_user()
        client.project_member_access_level("group/project", 99)
        methods = [call[0] for call in session.calls]
        self.assertIn("GET", methods)
        self.assertIn("PUT", methods)
        self.assertTrue(any("/notes" in call[1] for call in session.calls))
        self.assertTrue(
            any(call[2].get("json", {}).get("resolved") is False for call in session.calls)
        )

    def test_list_mr_discussions_follows_pagination(self) -> None:
        # The summary note lives on a later page of a busy MR; without pagination the
        # upsert never sees it and posts a duplicate summary each run.
        class PagedSession:
            def __init__(self) -> None:
                self.pages: dict[int, FakeResponse] = {
                    1: FakeResponse([{"id": "d1"}, {"id": "d2"}], headers={"X-Next-Page": "2"}),
                    2: FakeResponse([{"id": "summary-note"}], headers={"X-Next-Page": ""}),
                }
                self.requested_pages: list[int] = []

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                page = int(kwargs["params"]["page"])
                self.requested_pages.append(page)
                return self.pages[page]

        session = PagedSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        discussions = client.list_mr_discussions("group/project", 1)
        self.assertEqual([d["id"] for d in discussions], ["d1", "d2", "summary-note"])
        self.assertEqual(session.requested_pages, [1, 2])

    def test_pagination_stops_on_short_page_without_headers(self) -> None:
        # Mocked responses may omit pagination headers; a short page must end the loop
        # rather than requesting forever.
        class ShortPageSession:
            def __init__(self) -> None:
                self.count = 0

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                self.count += 1
                return FakeResponse([{"id": "only"}])

        session = ShortPageSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        self.assertEqual(len(client.list_mr_discussions("group/project", 1)), 1)
        self.assertEqual(session.count, 1)

    def test_pagination_cap_warns_on_truncation(self) -> None:
        # A server that always advertises a next page must stop at the 100-page cap and
        # warn about truncation rather than looping forever or truncating silently.
        class RunawaySession:
            def __init__(self) -> None:
                self.count = 0

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                self.count += 1
                return FakeResponse([{"id": self.count}], headers={"X-Next-Page": "999"})

        import contextlib
        import io

        session = RunawaySession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            items = client.list_mr_discussions("group/project", 1)
        self.assertEqual(session.count, 100)
        self.assertEqual(len(items), 100)
        self.assertIn("pagination cap reached", stderr.getvalue())

    def test_object_methods_reject_non_object_responses(self) -> None:
        # Mutating endpoints are consumed as objects by the posting pipeline; a
        # GitLab or mock response with another shape should fail at the I/O boundary.
        class ListResponseSession:
            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                return FakeResponse([])

        client = GitLabClient(
            "https://gitlab.example.com/api/v4", "token", session=ListResponseSession()
        )
        with self.assertRaisesRegex(GitLabApiError, "response was not an object"):
            client.create_mr_note("group/project", 1, "body")

    def test_paginated_methods_reject_non_object_items(self) -> None:
        # Pagination returns lists, but each discussion/note entry must be an object
        # before downstream state/indexing code sees it.
        class ScalarItemSession:
            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                return FakeResponse(["not-an-object"], headers={"X-Next-Page": ""})

        client = GitLabClient(
            "https://gitlab.example.com/api/v4", "token", session=ScalarItemSession()
        )
        with self.assertRaisesRegex(GitLabApiError, "returned a non-object item"):
            client.list_mr_discussions("group/project", 1)

    def test_fetch_mr_diff_degrades_on_empty_response(self) -> None:
        # Empty /diffs pages must degrade to an empty unified diff.
        class EmptyDiffsSession:
            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                response = FakeResponse([], status_code=200, headers={"X-Next-Page": ""})
                response.text = "[]"
                return response

        client = GitLabClient(
            "https://gitlab.example.com/api/v4", "token", session=EmptyDiffsSession()
        )
        self.assertEqual(client.fetch_mr_diff("group/project", 1), "\n")

    def test_fetch_mr_diff_follows_pagination(self) -> None:
        class PagedDiffSession:
            def __init__(self) -> None:
                self.requested_pages: list[int] = []

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                if "/diffs" not in url:
                    raise AssertionError(f"expected /diffs URL, got {url}")
                page = int(kwargs["params"]["page"])
                self.requested_pages.append(page)
                if page == 1:
                    return FakeResponse(
                        [
                            {
                                "old_path": "a.py",
                                "new_path": "a.py",
                                "diff": "@@ -1 +1 @@\n-old\n+new\n",
                            }
                        ],
                        headers={"X-Next-Page": "2"},
                    )
                return FakeResponse(
                    [
                        {
                            "old_path": "b.py",
                            "new_path": "b.py",
                            "diff": "@@ -0,0 +1 @@\n+x\n",
                        }
                    ],
                    headers={"X-Next-Page": ""},
                )

        session = PagedDiffSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        diff = client.fetch_mr_diff("group/project", 1)
        self.assertEqual(session.requested_pages, [1, 2])
        self.assertIn("diff --git a/a.py b/a.py", diff)
        self.assertIn("diff --git a/b.py b/b.py", diff)
        self.assertIn("@@ -1 +1 @@\n-old\n+new\n", diff)

    def test_fetch_mr_diff_recovers_collapsed_file_from_raw_changes(self) -> None:
        primary = [
            {
                "old_path": "small.py",
                "new_path": "small.py",
                "diff": "@@ -0,0 +1 @@\n+small\n",
            },
            {
                "old_path": "big.py",
                "new_path": "big.py",
                "diff": "",
                "collapsed": True,
            },
        ]
        recovered = {
            "old_path": "big.py",
            "new_path": "big.py",
            "diff": "@@ -1 +1 @@\n-old\n+new\n",
            "collapsed": False,
            "too_large": False,
        }
        session = DiffFallbackSession(
            primary, {"overflow": False, "changes": [recovered]}
        )
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            diff = client.fetch_mr_diff("group/project", 1)

        self.assertIn("diff --git a/small.py b/small.py", diff)
        self.assertIn("diff --git a/big.py b/big.py", diff)
        self.assertIn("@@ -1 +1 @@\n-old\n+new\n", diff)
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(session.calls[0][0].endswith("/diffs"))
        self.assertTrue(session.calls[1][0].endswith("/changes"))
        self.assertEqual(session.calls[1][1]["params"], {"access_raw_diffs": "true"})
        self.assertIn("recovered 1 GitLab raw diff(s): 'big.py'", stderr.getvalue())

    def test_fetch_mr_diff_recovers_too_large_file_from_raw_changes(self) -> None:
        primary = [
            {
                "old_path": "big.py",
                "new_path": "big.py",
                "diff": "",
                "too_large": True,
            }
        ]
        recovered = {
            "old_path": "big.py",
            "new_path": "big.py",
            "diff": "@@ -1 +1 @@\n-old\n+new\n",
            "too_large": False,
        }
        client = GitLabClient(
            "https://gitlab.example.com/api/v4",
            "token",
            session=DiffFallbackSession(
                primary, {"overflow": False, "changes": [recovered]}
            ),
        )
        with contextlib.redirect_stderr(io.StringIO()):
            diff = client.fetch_mr_diff("group/project", 1)

        self.assertIn("@@ -1 +1 @@\n-old\n+new\n", diff)

    def test_fetch_mr_diff_accepts_complete_empty_raw_diff(self) -> None:
        primary = [
            {
                "old_path": "binary.dat",
                "new_path": "binary.dat",
                "diff": "",
                "collapsed": True,
            }
        ]
        recovered = {
            "old_path": "binary.dat",
            "new_path": "binary.dat",
            "diff": "",
            "collapsed": False,
            "too_large": False,
        }
        client = GitLabClient(
            "https://gitlab.example.com/api/v4",
            "token",
            session=DiffFallbackSession(
                primary, {"overflow": False, "changes": [recovered]}
            ),
        )
        with contextlib.redirect_stderr(io.StringIO()):
            diff = client.fetch_mr_diff("group/project", 1)

        self.assertIn("diff --git a/binary.dat b/binary.dat", diff)

    def test_fetch_mr_diff_fails_when_raw_response_is_not_an_object(self) -> None:
        primary = [
            {
                "old_path": "big.py",
                "new_path": "big.py",
                "diff": "",
                "collapsed": True,
            }
        ]
        client = GitLabClient(
            "https://gitlab.example.com/api/v4",
            "token",
            session=DiffFallbackSession(primary, []),
        )
        with self.assertRaisesRegex(GitLabApiError, "non-object response"):
            client.fetch_mr_diff("group/project", 1)

    def test_fetch_mr_diff_fails_without_explicit_non_overflow_signal(self) -> None:
        primary = [
            {
                "old_path": "big.py",
                "new_path": "big.py",
                "diff": "",
                "collapsed": True,
            }
        ]
        for raw_payload in ({}, {"overflow": None}, {"overflow": True}):
            with self.subTest(raw_payload=raw_payload):
                client = GitLabClient(
                    "https://gitlab.example.com/api/v4",
                    "token",
                    session=DiffFallbackSession(primary, raw_payload),
                )
                with self.assertRaisesRegex(GitLabApiError, "non-overflowing response"):
                    client.fetch_mr_diff("group/project", 1)

    def test_fetch_mr_diff_fails_when_raw_changes_are_malformed(self) -> None:
        primary = [
            {
                "old_path": "big.py",
                "new_path": "big.py",
                "diff": "",
                "collapsed": True,
            }
        ]
        for changes in ({}, ["not-an-object"]):
            with self.subTest(changes=changes):
                client = GitLabClient(
                    "https://gitlab.example.com/api/v4",
                    "token",
                    session=DiffFallbackSession(
                        primary, {"overflow": False, "changes": changes}
                    ),
                )
                with self.assertRaisesRegex(GitLabApiError, "malformed changes"):
                    client.fetch_mr_diff("group/project", 1)

    def test_fetch_mr_diff_fails_when_primary_identity_is_duplicated(self) -> None:
        duplicate = {
            "old_path": "big.py",
            "new_path": "big.py",
            "diff": "",
            "collapsed": True,
        }
        session = DiffFallbackSession([duplicate, dict(duplicate)], {})
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)

        with self.assertRaisesRegex(GitLabApiError, "primary diff response returned duplicate"):
            client.fetch_mr_diff("group/project", 1)
        self.assertEqual(len(session.calls), 1)

    def test_fetch_mr_diff_fails_when_raw_change_is_missing(self) -> None:
        primary = [
            {
                "old_path": "big.py",
                "new_path": "big.py",
                "diff": "",
                "collapsed": True,
            }
        ]
        client = GitLabClient(
            "https://gitlab.example.com/api/v4",
            "token",
            session=DiffFallbackSession(primary, {"overflow": False, "changes": []}),
        )
        with self.assertRaisesRegex(GitLabApiError, "no matching change"):
            client.fetch_mr_diff("group/project", 1)

    def test_fetch_mr_diff_fails_when_raw_change_is_ambiguous(self) -> None:
        primary = [
            {
                "old_path": "big.py",
                "new_path": "big.py",
                "diff": "",
                "collapsed": True,
            }
        ]
        recovered = {
            "old_path": "big.py",
            "new_path": "big.py",
            "diff": "@@ -1 +1 @@\n-old\n+new\n",
        }
        client = GitLabClient(
            "https://gitlab.example.com/api/v4",
            "token",
            session=DiffFallbackSession(
                primary,
                {"overflow": False, "changes": [recovered, dict(recovered)]},
            ),
        )
        with self.assertRaisesRegex(GitLabApiError, "multiple matching changes"):
            client.fetch_mr_diff("group/project", 1)

    def test_fetch_mr_diff_fails_when_raw_change_remains_incomplete(self) -> None:
        change = {
            "old_path": "big.py",
            "new_path": "big.py",
            "diff": "",
            "too_large": True,
        }
        client = GitLabClient(
            "https://gitlab.example.com/api/v4",
            "token",
            session=DiffFallbackSession(
                [change], {"overflow": False, "changes": [change]}
            ),
        )
        with self.assertRaisesRegex(GitLabApiError, "remained incomplete"):
            client.fetch_mr_diff("group/project", 1)

    def test_fetch_mr_diff_fails_when_raw_diff_is_not_text(self) -> None:
        primary = [
            {
                "old_path": "big.py",
                "new_path": "big.py",
                "diff": "",
                "collapsed": True,
            }
        ]
        recovered = {
            "old_path": "big.py",
            "new_path": "big.py",
            "diff": None,
        }
        client = GitLabClient(
            "https://gitlab.example.com/api/v4",
            "token",
            session=DiffFallbackSession(
                primary, {"overflow": False, "changes": [recovered]}
            ),
        )
        with self.assertRaisesRegex(GitLabApiError, "did not return text diff content"):
            client.fetch_mr_diff("group/project", 1)

    def test_send_retries_idempotent_verbs_on_502(self) -> None:
        class FlakySession:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                self.calls += 1
                if self.calls < 3:
                    return FakeResponse({"error": "bad gateway"}, status_code=502)
                return FakeResponse({"id": 1}, status_code=200)

        session = FlakySession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        with mock.patch("ai_review.http_retry.sleep"):
            parsed = client._request("GET", "/projects/1")
        self.assertEqual(parsed, {"id": 1})
        self.assertEqual(session.calls, 3)

    def test_send_does_not_retry_post(self) -> None:
        class Always502Session:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                self.calls += 1
                return FakeResponse({"error": "bad gateway"}, status_code=502)

        session = Always502Session()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        with (
            mock.patch("ai_review.http_retry.sleep"),
            self.assertRaisesRegex(GitLabApiError, "502"),
        ):
            client._request("POST", "/projects/1/notes", json={"body": "x"})
        self.assertEqual(session.calls, 1)

    def test_send_normalizes_exhausted_connection_errors(self) -> None:
        class BoomSession:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                self.calls += 1
                raise ConnectionError("network down")

        session = BoomSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        with (
            mock.patch("ai_review.http_retry.sleep"),
            self.assertRaisesRegex(GitLabApiError, "connection error"),
        ):
            client._request("PUT", "/projects/1/notes/1", json={"body": "x"})
        self.assertEqual(session.calls, 3)

    def test_send_retries_and_normalizes_exhausted_proxy_errors(self) -> None:
        requests_connection_error = type(
            "ConnectionError",
            (Exception,),
            {"__module__": "requests.exceptions"},
        )
        proxy_error = type(
            "ProxyError",
            (requests_connection_error,),
            {"__module__": "requests.exceptions"},
        )

        class ProxyBoomSession:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
                self.calls += 1
                raise proxy_error("proxy down")

        session = ProxyBoomSession()
        client = GitLabClient("https://gitlab.example.com/api/v4", "token", session=session)
        with (
            mock.patch("ai_review.http_retry.sleep"),
            self.assertRaisesRegex(GitLabApiError, "connection error"),
        ):
            client._request("GET", "/projects/1")
        self.assertEqual(session.calls, 3)


if __name__ == "__main__":
    unittest.main()
