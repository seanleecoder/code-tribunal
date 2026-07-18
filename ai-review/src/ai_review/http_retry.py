"""Bounded retries for idempotent HTTP API calls."""

from __future__ import annotations

import time
from collections.abc import Callable

IDEMPOTENT_METHODS = frozenset({"GET", "PUT", "PATCH"})
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 2.0, 4.0)

# Tests patch this symbol to avoid real delays.
sleep = time.sleep


def is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def is_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    module = type(exc).__module__ or ""
    name = type(exc).__name__
    if module.startswith(("requests", "urllib3")):
        return name in {
            "ConnectionError",
            "ConnectTimeout",
            "ReadTimeout",
            "Timeout",
            "ChunkedEncodingError",
            "ProtocolError",
            "NewConnectionError",
            "MaxRetryError",
            "RequestException",
        }
    return False


def send_with_retries[T](
    *,
    method: str,
    do_request: Callable[[], T],
    get_status: Callable[[T], int],
    make_http_error: Callable[[int], Exception],
    max_attempts: int = MAX_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = BACKOFF_SECONDS,
) -> T:
    """Execute an HTTP request with bounded retries for idempotent methods.

    POST (and other non-idempotent verbs) are never retried: a timeout after
    server-side success would duplicate create-side effects such as review threads.
    """
    method_upper = method.upper()
    retryable = method_upper in IDEMPOTENT_METHODS
    attempts = max_attempts if retryable else 1

    for attempt_index in range(attempts):
        try:
            response = do_request()
        except Exception as exc:
            if (
                not retryable
                or not is_connection_error(exc)
                or attempt_index >= attempts - 1
            ):
                raise
            sleep(backoff_seconds[min(attempt_index, len(backoff_seconds) - 1)])
            continue

        status = get_status(response)
        if status < 400:
            return response
        if (
            not retryable
            or not is_retryable_status(status)
            or attempt_index >= attempts - 1
        ):
            raise make_http_error(status)
        sleep(backoff_seconds[min(attempt_index, len(backoff_seconds) - 1)])

    raise AssertionError("send_with_retries exhausted attempts without returning")
