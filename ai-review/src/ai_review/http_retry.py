"""Bounded retries for idempotent HTTP API calls."""

from __future__ import annotations

import time
from collections.abc import Callable

IDEMPOTENT_METHODS = frozenset({"GET", "PUT", "PATCH"})
MAX_ATTEMPTS = 3
# TODO(SPEC-30 follow-up): honor Retry-After on 429 so fixed backoff cannot
# immediately re-enter the same rate-limit window.
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
    # Enumerate transient transport failures only. Do not treat the requests
    # base RequestException as retryable — it also covers permanent errors
    # such as InvalidURL, MissingSchema, and TooManyRedirects.
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
        }
    return False


def send_with_retries[T](
    *,
    method: str,
    do_request: Callable[[], T],
    get_status: Callable[[T], int],
    make_http_error: Callable[[int], Exception],
    make_connection_error: Callable[[BaseException], Exception] | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    backoff_seconds: tuple[float, ...] = BACKOFF_SECONDS,
) -> T:
    """Execute an HTTP request with bounded retries for idempotent methods.

    POST (and other non-idempotent verbs) are never retried: a timeout after
    server-side success would duplicate create-side effects such as review threads.

    When ``make_connection_error`` is provided, exhausted (or non-retryable)
    connection failures are normalized through that factory so callers can
    surface platform-specific errors instead of raw transport exceptions.
    """
    method_upper = method.upper()
    retryable = method_upper in IDEMPOTENT_METHODS
    attempts = max_attempts if retryable else 1

    for attempt_index in range(attempts):
        try:
            response = do_request()
        except Exception as exc:
            if (
                retryable
                and is_connection_error(exc)
                and attempt_index < attempts - 1
            ):
                sleep(backoff_seconds[min(attempt_index, len(backoff_seconds) - 1)])
                continue
            if is_connection_error(exc) and make_connection_error is not None:
                raise make_connection_error(exc) from exc
            raise

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
