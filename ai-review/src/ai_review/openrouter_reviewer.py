from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from .redact import redact_text

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_SYSTEM_JSON_INSTRUCTION = (
    "Output only a single JSON object matching the contract described in the prompt. "
    "Do not include markdown fences, prose, or explanations outside the JSON object."
)

# (url, headers, body) -> (status_code, response_text)
Transport = Callable[[str, dict[str, str], dict[str, Any]], tuple[int, str]]


class OpenRouterError(RuntimeError):
    pass


def build_request_payload(
    prompt: str, model: str, *, temperature: float = 0.0, json_mode: bool = True
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": _SYSTEM_JSON_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    return payload


def extract_content(response_text: str) -> str:
    try:
        payload = json.loads(response_text)
        return str(payload["choices"][0]["message"]["content"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise OpenRouterError(f"unexpected OpenRouter response envelope: {exc}") from exc


def _base_url(env: Mapping[str, str]) -> str:
    base = env.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL
    return base.rstrip("/")


def _headers(env: Mapping[str, str], api_key: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    referer = env.get("OPENROUTER_HTTP_REFERER")
    if referer:
        headers["HTTP-Referer"] = referer
    title = env.get("OPENROUTER_X_TITLE")
    if title:
        headers["X-Title"] = title
    return headers


def _urllib_transport(url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def run(
    reviewer: str,
    stage: str,
    *,
    transport: Transport = _urllib_transport,
    env: Mapping[str, str] | None = None,
) -> int:
    env = env if env is not None else os.environ

    api_key = env.get("OPENROUTER_API_KEY", "")
    if not api_key:
        sys.stderr.write(
            redact_text(f"openrouter_reviewer: OPENROUTER_API_KEY is required for {reviewer}\n")
        )
        return 1

    prompt_path = env.get("AI_REVIEW_RENDERED_PROMPT", "")
    if not prompt_path or not os.path.exists(prompt_path):
        sys.stderr.write(
            f"openrouter_reviewer: AI_REVIEW_RENDERED_PROMPT not found for {reviewer}/{stage}\n"
        )
        return 1
    with open(prompt_path, encoding="utf-8") as handle:
        prompt = handle.read()

    model = env.get("AI_REVIEW_MODEL", "")
    if not model:
        sys.stderr.write(f"openrouter_reviewer: AI_REVIEW_MODEL is required for {reviewer}\n")
        return 1

    url = f"{_base_url(env)}/chat/completions"
    headers = _headers(env, api_key)

    try:
        status, text = transport(url, headers, build_request_payload(prompt, model))
        if status == 400 and "response_format" in text:
            status, text = transport(
                url, headers, build_request_payload(prompt, model, json_mode=False)
            )
        if status >= 400:
            sys.stderr.write(
                redact_text(f"openrouter_reviewer: OpenRouter HTTP {status}: {text[:500]}\n")
            )
            return 1
        content = extract_content(text)
    except (OpenRouterError, urllib.error.URLError, OSError) as exc:
        sys.stderr.write(redact_text(f"openrouter_reviewer: request failed: {exc}\n"))
        return 1

    sys.stdout.write(content)
    return 0


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewer")
    parser.add_argument("stage", choices=["review", "critique", "respond"])
    args = parser.parse_args(argv)
    return run(args.reviewer, args.stage)


if __name__ == "__main__":
    raise SystemExit(cli())
