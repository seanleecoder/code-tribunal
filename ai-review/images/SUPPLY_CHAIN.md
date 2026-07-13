# AI Review image supply-chain pins

The reviewer image keeps mutable package inputs in reviewed repository files:

- `package.json` and `package-lock.json` pin reviewer CLIs and npm integrity metadata.
- `python-constraints.txt` pins the direct and transitive Python packages installed into the base image.
- `base.Dockerfile` pins the Python base image by digest, and `reviewer.Dockerfile` uses the same digest as its standalone-build default.
- `.github/workflows/publish-ai-review-images.yml` pins GitHub Actions by full commit SHA with comments naming the tracked upstream tag.

## Refresh process

1. Update npm CLI versions in `package.json` and regenerate the lockfile from `ai-review/images` with `npm install --package-lock-only`.
2. Update Python pins in `python-constraints.txt` from a clean resolver after reviewing upstream release notes.
3. Refresh the base image digest with a registry manifest inspection, for example `docker buildx imagetools inspect python:3.12-slim-bookworm`, and update both `base.Dockerfile` and the `AI_REVIEW_BASE_IMAGE` default in `reviewer.Dockerfile` to the same digest.
4. Refresh the pinned Node builder digest in `reviewer.Dockerfile` when intentionally changing the builder image.
5. Refresh action SHAs from the upstream action tag, keeping the adjacent comment with the human-readable tag.
6. Run `python scripts/check_supply_chain_pins.py` and the image build/preflight workflow.

## Residual apt limits

The base image installs Debian `ca-certificates` and `git` from the Bookworm apt repositories without exact package-version pins. Apt repository snapshots would improve byte-for-byte rebuilds, but add mirror operations and security-update latency. The pinned base-image digest and Python/npm lock inputs keep the application-layer tools reproducible; apt drift is limited to explicit rebuilds after the base digest is intentionally refreshed.
