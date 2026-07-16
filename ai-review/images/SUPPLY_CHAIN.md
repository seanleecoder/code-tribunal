# AI Review image supply-chain pins

The reviewer image keeps mutable package inputs in reviewed repository files:

- `package.json` and `package-lock.json` pin npm-distributed reviewer CLIs and npm integrity metadata.
- `cursor-agent.pin` records the Cursor CLI version, download URL, and SHA-256 because Cursor CLI is distributed outside npm.
- `python-constraints.txt` pins the direct and transitive Python packages installed into the base image.
- `base.Dockerfile` pins the Python base image by digest, and `reviewer.Dockerfile` uses the same digest as its standalone-build default.
- `.github/workflows/publish-ai-review-images.yml` pins GitHub Actions by full commit SHA with comments naming the tracked upstream tag.

## Refresh process

1. Update npm CLI versions in `package.json` and regenerate the lockfile from `ai-review/images` with `npm install --package-lock-only`.
2. Update Python pins in `python-constraints.txt` from a clean resolver after reviewing upstream release notes.
3. Refresh the base image digest with a registry manifest inspection, for example `docker buildx imagetools inspect python:3.12-slim-bookworm`, and update both `base.Dockerfile` and the `AI_REVIEW_BASE_IMAGE` default in `reviewer.Dockerfile` to the same digest.
4. Refresh the pinned Node builder digest in `reviewer.Dockerfile` when intentionally changing the builder image.
5. Refresh `cursor-agent.pin` by selecting a versioned `downloads.cursor.com` artifact, recording its SHA-256, and rebuilding the reviewer image. If Cursor only exposes a moving installer for a release window, run the installer in a builder, hash the produced binary/archive, and document the weaker provenance in the pin-review commit.
6. Refresh action SHAs from the upstream action tag, keeping the adjacent comment with the human-readable tag.
7. Run `python scripts/check_supply_chain_pins.py` and the image build/preflight workflow.
8. After the trusted `main` publication succeeds, copy the source commit and both digests from the workflow summary. Update `.github/workflows/ai-review.yml`, `ai-review/ci/review.github-actions.yml`, and `ai-review/ci/review.gitlab-ci.yml` together; update `AI_REVIEW_TRUSTED_IMAGE_SHA` in the GitLab template to that same source commit. Digest changes remain reviewed rather than being committed automatically by the publishing workflow.
9. For every `cursor-agent.pin` bump, confirm that **Verify Cursor denies write and shell tools** passed for the published image before setting `AI_REVIEW_CURSOR_ENABLED=true`. Also retry `--sandbox enabled` in the nested-container preflight; remove the allowlist exception if the new CLI can initialize its kernel sandbox there.

## Residual apt limits

The base image installs Debian `ca-certificates` and `git` from the Bookworm apt repositories without exact package-version pins. Apt repository snapshots would improve byte-for-byte rebuilds, but add mirror operations and security-update latency. The pinned base-image digest and Python/npm lock inputs keep the application-layer tools reproducible; apt drift is limited to explicit rebuilds after the base digest is intentionally refreshed.

## Cursor CLI egress exception

Cursor CLI cannot use OpenRouter or a custom base URL in agent mode. The default config keeps `reviewers.cursor.enabled: false`; when operators opt in, prompts, MR diffs, and readable repo-snapshot content are sent to Cursor's backend and billed through the Cursor account associated with `CURSOR_API_KEY`. The runner injects only the reviewer's declared credential and `adapters/cursor.sh` runs under `env -i`, so `OPENROUTER_API_KEY` is not forwarded to Cursor.

## Cursor sandbox exception

The pinned Cursor CLI's kernel sandbox is unavailable inside nested GitHub Actions job containers. The adapter therefore selects allowlist mode on each print invocation with `--sandbox disabled --trust`, avoiding a separate state-mutating setup command. Isolation for this reviewer depends on the sanitized disposable workspace plus `cli-config.json`, which allows `Read(**)` and denies `Write(**)` and `Shell(**)`. This is an explicit, weaker tradeoff rather than an equivalent replacement for kernel isolation. Before publishing an image from trusted `main`, `scripts/smoke_cursor_permissions.sh` gives the real pinned CLI a hostile prompt that probes workspace creation and overwrite, redirected-home writes, and temporary-path shell effects. It distinguishes detected filesystem mutation from a CLI execution error and prints bounded diagnostic output. Pull requests never receive `CURSOR_API_KEY` for this smoke. The smoke does not claim to prove network isolation; container-level egress remains the H2 limitation. Keep Cursor disabled for consumers until the trusted-main check passes.
