from __future__ import annotations

import unittest

from ai_review.http_retry import is_connection_error


class HttpRetryClassificationTests(unittest.TestCase):
    def test_builtin_connection_errors_are_retryable(self) -> None:
        self.assertTrue(is_connection_error(ConnectionError("down")))
        self.assertTrue(is_connection_error(TimeoutError("slow")))

    def test_permanent_requests_errors_are_not_retryable(self) -> None:
        class InvalidURL(Exception):
            __module__ = "requests.exceptions"

        class RequestException(Exception):
            __module__ = "requests.exceptions"

        self.assertFalse(is_connection_error(InvalidURL("bad url")))
        self.assertFalse(is_connection_error(RequestException("base class")))


if __name__ == "__main__":
    unittest.main()
