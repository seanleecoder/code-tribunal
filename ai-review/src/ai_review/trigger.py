from __future__ import annotations


def sanitize_flow_input(value: str, *, max_length: int = 4000) -> str:
    return "".join(char for char in value if char == "\n" or char == "\t" or ord(char) >= 32)[
        :max_length
    ]
