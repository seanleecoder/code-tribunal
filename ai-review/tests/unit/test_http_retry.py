from __future__ import annotations

import unittest

from ai_review.http_retry import is_connection_error


class HttpRetryClassificationTests(unittest.TestCase):
    def test_builtin_connection_errors_are_retryable(self) -> None:
        self.assertTrue(is_connection_error(ConnectionError("down")))
        self.assertTrue(is_connection_error(TimeoutError("slow")))

    def test_requests_proxy_error_is_retryable_via_hierarchy(self) -> None:
        # Mirror requests.exceptions: ProxyError subclasses ConnectionError.
        # Recognition must follow the MRO, not only the leaf type name.
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
        self.assertTrue(is_connection_error(proxy_error("proxy down")))

        # Even if the leaf name were unknown, the ConnectionError parent matches.
        odd_proxy = type(
            "CorporateProxyFailure",
            (requests_connection_error,),
            {"__module__": "requests.exceptions"},
        )
        self.assertTrue(is_connection_error(odd_proxy("proxy down")))

    def test_permanent_requests_errors_are_not_retryable(self) -> None:
        class InvalidURL(Exception):
            __module__ = "requests.exceptions"

        class RequestException(Exception):
            __module__ = "requests.exceptions"

        self.assertFalse(is_connection_error(InvalidURL("bad url")))
        self.assertFalse(is_connection_error(RequestException("base class")))


if __name__ == "__main__":
    unittest.main()
