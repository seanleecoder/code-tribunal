from __future__ import annotations

import json
from typing import Any
from unittest import mock

from ai_review.memory import encode_state_note
from ai_review.platform.github import (
    STATE_MARKER,
    GitHubReviewPlatform,
    GitHubReviewPlatformError,
)


class Response:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self) -> Any:
        return self._payload


class Session:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append((method, url, kwargs))
        if url.endswith("/user"):
            return Response(200, {"id": 42, "login": "bot"})
        if url.endswith("/users/github-actions%5Bbot%5D"):
            return Response(200, {"id": 41898282, "login": "github-actions[bot]"})
        if url.endswith("/issues/7/comments"):
            state = encode_state_note(
                {
                    "state_schema_version": 1,
                    "project_id": "octo/repo",
                    "merge_request_iid": "7",
                    "last_head_sha": "h" * 40,
                    "records": [],
                }
            )
            return Response(
                200,
                [
                    {
                        "id": 1,
                        "body": f"{state}\n\n{STATE_MARKER}",
                        "user": {"id": 42, "login": "bot"},
                    },
                    {
                        "id": 2,
                        "body": f"{state}\n\n{STATE_MARKER}",
                        "user": {"id": 99, "login": "alice"},
                    },
                    {"id": 3, "body": "summary", "user": {"id": 42, "login": "bot"}},
                ],
            )
        if url.endswith("/collaborators/alice/permission"):
            return Response(200, {"permission": "write"})
        if url.endswith("/repos/octo/repo/pulls/7"):
            return Response(
                200,
                {
                    "number": 7,
                    "head": {"sha": "1" * 40, "ref": "feature"},
                    "base": {"sha": "0" * 40, "ref": "main"},
                },
            )
        raise AssertionError(f"unexpected request: {method} {url}")


class DiffSession:
    def __init__(self, diff: str) -> None:
        self.diff = diff
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append((method, url, kwargs))
        response = Response(200, None)
        response.text = self.diff
        return response


class StateWriteSession:
    def __init__(self, author_login: str) -> None:
        self.author_login = author_login

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        if method in {"POST", "PATCH"}:
            return Response(
                200,
                {
                    "id": 17,
                    "body": kwargs["json"]["body"],
                    "user": {"id": 42, "login": self.author_login},
                },
            )
        raise AssertionError(f"unexpected request: {method} {url}")


class MissingBotSession:
    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        return Response(404, {"message": "Not Found"})


def test_state_notes_are_author_verified_and_normalized() -> None:
    session = Session()
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    notes = platform.list_state_notes("octo/repo", 7)

    assert len(notes) == 1
    assert notes[0]["id"] == 1
    assert notes[0]["author"]["id"] == 42
    assert notes[0]["author"]["username"] == "bot"


def test_member_access_level_maps_github_write_permissions() -> None:
    session = Session()
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    assert platform.member_access_level("octo/repo", "alice") == 40
    assert platform.member_access_level("octo/repo", 99) is None


def test_current_user_uses_configured_bot_login_for_installation_token() -> None:
    session = Session()
    platform = GitHubReviewPlatform(
        "https://api.github.test",
        "token",
        bot_login="github-actions[bot]",
        session=session,
    )

    assert platform.current_user_id() == 41898282
    assert session.calls[-1][1].endswith("/users/github-actions%5Bbot%5D")


def test_fetch_diff_returns_raw_patch_text() -> None:
    diff = "diff --git a/a.py b/a.py\n+print('ok')\n"
    session = DiffSession(diff)
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    assert platform.fetch_diff("octo/repo", 7) == diff
    _, _, kwargs = session.calls[0]
    assert kwargs["headers"]["Accept"] == "application/vnd.github.v3.diff"


def test_fetch_diff_returns_empty_string_for_empty_response() -> None:
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=DiffSession(""))

    assert platform.fetch_diff("octo/repo", 7) == ""


def test_fetch_pull_request_returns_metadata() -> None:
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=Session())

    pull_request = platform.fetch_pull_request("octo/repo", 7)

    assert pull_request["number"] == 7
    assert pull_request["head"]["ref"] == "feature"


def test_state_write_requires_configured_bot_to_be_actual_author() -> None:
    platform = GitHubReviewPlatform(
        "https://api.github.test",
        "token",
        bot_login="expected[bot]",
        session=StateWriteSession("different[bot]"),
    )

    try:
        platform.create_state_note("octo/repo", 7, "state")
    except GitHubReviewPlatformError as exc:
        assert "expected configured bot login" in str(exc)
    else:
        raise AssertionError("state write accepted a mismatched configured bot login")


def test_state_write_accepts_configured_bot_author() -> None:
    platform = GitHubReviewPlatform(
        "https://api.github.test",
        "token",
        bot_login="expected[bot]",
        session=StateWriteSession("expected[bot]"),
    )

    note = platform.update_state_note("octo/repo", 7, 17, "state")

    assert note["author"]["username"] == "expected[bot]"


def test_missing_configured_bot_has_explicit_error() -> None:
    platform = GitHubReviewPlatform(
        "https://api.github.test",
        "token",
        bot_login="missing[bot]",
        session=MissingBotSession(),
    )

    try:
        platform.current_user_id()
    except GitHubReviewPlatformError as exc:
        assert "could not be resolved" in str(exc)
    else:
        raise AssertionError("missing configured bot login did not fail")


def test_list_threads_groups_replies() -> None:
    class GroupingSession:
        def request(self, method: str, url: str, **kwargs: Any) -> Response:
            if url.endswith("/pulls/7/comments"):
                return Response(
                    200,
                    [
                        {"id": 1, "body": "root1", "created_at": "2023-01-01T00:00:00Z"},
                        {
                            "id": 2,
                            "body": "later reply to 1",
                            "in_reply_to_id": 1,
                            "created_at": "2023-01-01T00:00:02Z",
                        },
                        {"id": 3, "body": "root3", "created_at": "2023-01-01T00:00:00Z"},
                        {
                            "id": 5,
                            "body": "earlier reply to 1",
                            "in_reply_to_id": 1,
                            "created_at": "2023-01-01T00:00:01Z",
                        },
                        {
                            "id": 6,
                            "body": "reply to 3",
                            "in_reply_to_id": 3,
                            "created_at": "2023-01-01T00:00:01Z",
                        },
                        {
                            "id": 4,
                            "body": "orphan reply",
                            "in_reply_to_id": 999,
                            "created_at": "2023-01-01T00:00:03Z",
                        },
                    ],
                )
            if url.endswith("/issues/7/comments"):
                return Response(200, [])
            return Response(404, None)

    platform = GitHubReviewPlatform("https://api.github.test", "token", session=GroupingSession())
    threads = platform.list_threads("octo/repo", 7)

    assert len(threads) == 3

    t1 = next(t for t in threads if t["notes"][0]["id"] == 1)
    assert [note["id"] for note in t1["notes"]] == [1, 5, 2]

    t2 = next(t for t in threads if t["notes"][0]["id"] == 3)
    assert [note["id"] for note in t2["notes"]] == [3, 6]

    t3 = next(t for t in threads if t["notes"][0]["id"] == 4)
    assert len(t3["notes"]) == 1


class GraphQLSession:
    def __init__(
        self,
        thread_found: bool = True,
        *,
        lookup_response: Any | None = None,
        mutation_response: Any | None = None,
        mutation_status_code: int = 200,
        thread_node_ids: dict[int, str] | None = None,
    ) -> None:
        self.thread_found = thread_found
        self.lookup_response = lookup_response
        self.mutation_response = mutation_response
        self.mutation_status_code = mutation_status_code
        self.thread_node_ids = (
            thread_node_ids
            if thread_node_ids is not None
            else ({123: "node-1"} if thread_found else {})
        )
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append((method, url, kwargs))
        if url.endswith("/graphql"):
            query = kwargs.get("json", {}).get("query", "")
            if "query(" in query:
                if self.lookup_response is not None:
                    return Response(200, self.lookup_response)
                nodes = [
                    {"id": node_id, "comments": {"nodes": [{"databaseId": database_id}]}}
                    for database_id, node_id in self.thread_node_ids.items()
                ]
                return Response(
                    200,
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "pageInfo": {"hasNextPage": False},
                                        "nodes": nodes,
                                    }
                                }
                            }
                        }
                    },
                )
            elif "mutation(" in query:
                if self.mutation_response is not None:
                    return Response(self.mutation_status_code, self.mutation_response)
                mutation_key = (
                    "unresolveReviewThread"
                    if "unresolveReviewThread" in query
                    else "resolveReviewThread"
                )
                node_id = kwargs["json"]["variables"]["threadId"]
                return Response(
                    200,
                    {
                        "data": {
                            mutation_key: {
                                "thread": {
                                    "id": node_id,
                                    "isResolved": mutation_key == "resolveReviewThread",
                                }
                            }
                        }
                    },
                )
        return Response(404, None)


def test_resolve_thread_uses_graphql_mutation() -> None:
    session = GraphQLSession(thread_found=True)
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    thread = platform.resolve_thread("octo/repo", 7, "123")

    assert thread["id"] == "123"
    assert session.calls[-1][2]["json"]["variables"]["threadId"] == "node-1"
    assert session.calls[-1][2]["headers"]["Authorization"] == "Bearer token"


def test_resolve_thread_uses_dedicated_token_only_for_mutation() -> None:
    session = GraphQLSession(thread_found=True)
    platform = GitHubReviewPlatform(
        "https://api.github.test",
        "primary-token",
        resolution_token="resolution-token",
        session=session,
    )

    platform.resolve_thread("octo/repo", 7, "123")

    lookup, mutation = session.calls
    assert lookup[2]["headers"]["Authorization"] == "Bearer primary-token"
    assert mutation[2]["headers"]["Authorization"] == "Bearer resolution-token"


def test_resolve_thread_caches_thread_map_across_mutations() -> None:
    session = GraphQLSession(thread_node_ids={123: "node-1", 456: "node-2"})
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    platform.resolve_thread("octo/repo", 7, "123")
    platform.resolve_thread("octo/repo", 7, "456")

    lookups = [call for call in session.calls if "query(" in call[2]["json"]["query"]]
    mutations = [call for call in session.calls if "mutation(" in call[2]["json"]["query"]]
    assert len(lookups) == 1
    assert len(mutations) == 2


def test_resolve_thread_refreshes_cached_map_once_on_miss() -> None:
    session = GraphQLSession(thread_node_ids={123: "node-1"})
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)
    platform.resolve_thread("octo/repo", 7, "123")
    session.thread_node_ids[456] = "node-2"

    platform.resolve_thread("octo/repo", 7, "456")

    lookups = [call for call in session.calls if "query(" in call[2]["json"]["query"]]
    assert len(lookups) == 2


def test_resolve_thread_uses_ghes_graphql_endpoint() -> None:
    session = GraphQLSession()
    platform = GitHubReviewPlatform(
        "https://github.example/api/v3", "token", session=session
    )

    platform.resolve_thread("octo/repo", 7, "123")

    assert session.calls[0][1] == "https://github.example/api/graphql"


def test_resolve_thread_normalizes_invalid_repository_and_pr() -> None:
    platform = GitHubReviewPlatform(
        "https://api.github.test", "token", session=GraphQLSession()
    )

    for repository, change_id in (("invalid", 7), ("octo/repo", "not-a-number")):
        try:
            platform.resolve_thread(repository, change_id, "123")
        except GitHubReviewPlatformError:
            pass
        else:
            raise AssertionError("invalid repository or PR escaped platform error handling")


def test_resolve_thread_raises_error_if_not_found() -> None:
    session = GraphQLSession(thread_found=False)
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "not found via GraphQL" in str(exc)
    else:
        raise AssertionError("missing thread did not raise")


def test_resolve_thread_normalizes_graphql_lookup_errors() -> None:
    session = GraphQLSession(lookup_response={"data": None, "errors": [{"message": "denied"}]})
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "review thread lookup failed" in str(exc)
        assert "denied" in str(exc)
    else:
        raise AssertionError("GraphQL lookup error was accepted")


def test_resolve_thread_rejects_null_graphql_lookup_data() -> None:
    session = GraphQLSession(lookup_response={"data": None})
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "did not contain data" in str(exc)
    else:
        raise AssertionError("null GraphQL lookup data was accepted")


def test_resolve_thread_rejects_null_mutation_payload() -> None:
    session = GraphQLSession(mutation_response={"data": {"resolveReviewThread": None}})
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "returned no thread" in str(exc)
    else:
        raise AssertionError("null GraphQL mutation payload was accepted")


def test_resolve_thread_normalizes_graphql_mutation_errors() -> None:
    session = GraphQLSession(
        mutation_response={"data": {"resolveReviewThread": None}, "errors": [{"message": "denied"}]}
    )
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "review thread resolve failed" in str(exc)
        assert "denied" in str(exc)
    else:
        raise AssertionError("GraphQL mutation error was accepted")


def test_resolve_thread_hints_when_builtin_token_lacks_permission() -> None:
    session = GraphQLSession(
        mutation_response={
            "data": {"resolveReviewThread": None},
            "errors": [{"message": "Resource not accessible by integration"}],
        }
    )
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "AI_REVIEW_GITHUB_RESOLVE_TOKEN" in str(exc)
        assert "Pull requests read/write" in str(exc)
    else:
        raise AssertionError("GraphQL permission error was accepted")


def test_resolve_thread_does_not_blame_missing_secret_when_dedicated_token_fails() -> None:
    session = GraphQLSession(
        mutation_response={
            "data": {"resolveReviewThread": None},
            "errors": [{"message": "Resource not accessible by integration"}],
        }
    )
    platform = GitHubReviewPlatform(
        "https://api.github.test",
        "token",
        resolution_token="resolve-token",
        session=session,
    )

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "AI_REVIEW_GITHUB_RESOLVE_TOKEN" not in str(exc)
    else:
        raise AssertionError("GraphQL permission error was accepted")


def test_resolve_thread_hints_when_builtin_token_gets_http_403() -> None:
    session = GraphQLSession(
        mutation_response={"message": "Forbidden"},
        mutation_status_code=403,
    )
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "failed: 403" in str(exc)
        assert "may lack review-thread mutation permission" in str(exc)
        assert "optional AI_REVIEW_GITHUB_RESOLVE_TOKEN" in str(exc)
    else:
        raise AssertionError("HTTP 403 mutation response was accepted")


def test_resolve_thread_keeps_http_403_generic_with_dedicated_token() -> None:
    session = GraphQLSession(
        mutation_response={"message": "Forbidden"},
        mutation_status_code=403,
    )
    platform = GitHubReviewPlatform(
        "https://api.github.test",
        "token",
        resolution_token="resolve-token",
        session=session,
    )

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "failed: 403" in str(exc)
        assert "AI_REVIEW_GITHUB_RESOLVE_TOKEN" not in str(exc)
    else:
        raise AssertionError("HTTP 403 mutation response was accepted")


def test_resolve_thread_rejects_unexpected_mutation_state() -> None:
    session = GraphQLSession(
        mutation_response={
            "data": {
                "resolveReviewThread": {"thread": {"id": "node-1", "isResolved": False}}
            }
        }
    )
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    try:
        platform.resolve_thread("octo/repo", 7, "123")
    except GitHubReviewPlatformError as exc:
        assert "unexpected thread state" in str(exc)
    else:
        raise AssertionError("unexpected GraphQL mutation state was accepted")


def test_unresolve_thread_validates_successful_mutation() -> None:
    session = GraphQLSession()
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)

    thread = platform.resolve_thread("octo/repo", 7, "123", resolved=False)

    assert thread["resolved"] is False
    assert "unresolveReviewThread" in session.calls[-1][2]["json"]["query"]


def test_request_retries_idempotent_verbs_on_502() -> None:
    class FlakySession:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, method: str, url: str, **kwargs: Any) -> Response:
            self.calls += 1
            if self.calls < 3:
                return Response(502, {"message": "bad gateway"})
            return Response(200, {"ok": True})

    session = FlakySession()
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)
    with mock.patch("ai_review.http_retry.sleep"):
        payload = platform._request("GET", "/repos/octo/repo")
    assert payload == {"ok": True}
    assert session.calls == 3


def test_request_does_not_retry_post() -> None:
    class Always502Session:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, method: str, url: str, **kwargs: Any) -> Response:
            self.calls += 1
            return Response(502, {"message": "bad gateway"})

    session = Always502Session()
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)
    with mock.patch("ai_review.http_retry.sleep"):
        try:
            platform._request("POST", "/repos/octo/repo/issues/7/comments", json={"body": "x"})
        except GitHubReviewPlatformError as exc:
            assert "502" in str(exc)
        else:
            raise AssertionError("POST should fail without retrying")
    assert session.calls == 1


def test_request_normalizes_exhausted_connection_errors() -> None:
    class BoomSession:
        def __init__(self) -> None:
            self.calls = 0

        def request(self, method: str, url: str, **kwargs: Any) -> Response:
            self.calls += 1
            raise ConnectionError("network down")

    session = BoomSession()
    platform = GitHubReviewPlatform("https://api.github.test", "token", session=session)
    with mock.patch("ai_review.http_retry.sleep"):
        try:
            platform._request("PATCH", "/repos/octo/repo/pulls/comments/1", json={"body": "x"})
        except GitHubReviewPlatformError as exc:
            assert "connection error" in str(exc)
        else:
            raise AssertionError("exhausted connection retries should raise platform error")
    assert session.calls == 3
