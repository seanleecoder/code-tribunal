# Contributing

Thanks for helping improve Code Tribunal.

## Development Setup

```bash
# From the repository root:
python3 -m pip install -r requirements-dev.txt
export PYTHONPATH="$PWD/ai-review/src"
```

The Python source under `ai-review/src` is an internal implementation used by
the shipped container images. It is loaded directly from the checkout during
development and is not an installable or supported Python distribution.

Run the local quality checks before opening a pull request:

```bash
make quality
```

This is the same blocking command used by CI. It runs Ruff over the package,
tests, and shipped scripts; pytest with coverage; whole-package mypy; the
supply-chain pin audit; and Python compilation checks. For a minimal local
environment, `make test` uses unittest only when pytest is not installed. An
installed pytest failure is never retried through the fallback, and
`make quality` never uses it.

## Pull Request Checklist

- Summarize the change and link the finding/spec ID when applicable.
- Add or update tests for behavior changes.
- Keep the canonical `make quality` gate green.
- Document new configuration and mark reserved/inert options honestly.
- Avoid exposing GitLab, OpenRouter, Anthropic, or reviewer CLI tokens in logs or posted comments.

## Adding a Reviewer Backend

Reviewer backends are wired through `ai-review/config/review.yaml`, `ai-review/src/ai_review/adapter_runner.py`, and a shell adapter under `ai-review/adapters/`. New backends should validate model IDs, pin provider endpoints exactly, sanitize environment variables, and produce schema-valid finding/critique artifacts.
