"""The access helper contract used by both demo projects."""

DEMO_ACCESS_SOURCE = """def normalize_username(value: str) -> str:
    return value.strip().casefold()


def is_allowed(username: str, allowed_users: set[str]) -> bool:
    normalized_allowed = {normalize_username(item) for item in allowed_users}
    return normalize_username(username) in normalized_allowed
"""
