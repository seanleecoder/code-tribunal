# Contributing

Thanks for helping improve Code Tribunal.

## Development Setup

```bash
python -m pip install -e '.[dev]'
```

Run the local quality checks before opening a pull request:

```bash
make test
ruff check ai-review/src ai-review/tests
mypy ai-review/src/ai_review/consensus.py ai-review/src/ai_review/memory.py ai-review/src/ai_review/render.py ai-review/src/ai_review/schema.py ai-review/src/ai_review/anchors.py ai-review/src/ai_review/gitlab_client.py ai-review/src/ai_review/gate.py ai-review/src/ai_review/post.py
mypy  # non-blocking whole-package debt signal; remaining known debt is outside the SPEC-13/14 strict slice
```

## Pull Request Checklist

- Summarize the change and link the finding/spec ID when applicable.
- Add or update tests for behavior changes.
- Keep `ruff` and `pytest` green; the scoped SPEC-13/14 `mypy` command is blocking, and the whole-package `mypy` command remains a visible non-blocking CI signal while remaining package-wide typing debt is closed incrementally.
- Document new configuration and mark reserved/inert options honestly.
- Avoid exposing GitLab, OpenRouter, Anthropic, or reviewer CLI tokens in logs or posted comments.

## Adding a Reviewer Backend

Reviewer backends are wired through `ai-review/config/review.yaml`, `ai-review/src/ai_review/adapter_runner.py`, and a shell adapter under `ai-review/adapters/`. New backends should validate model IDs, pin provider endpoints exactly, sanitize environment variables, and produce schema-valid finding/critique artifacts.
