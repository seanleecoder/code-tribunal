ARG AI_REVIEW_BASE_IMAGE
FROM node:22-bookworm-slim AS reviewer-clis

ARG CLAUDE_VERSION
ARG CODEX_VERSION
ARG OPENCODE_VERSION
ARG CLAUDE_NPM_PACKAGE=@anthropic-ai/claude-code
ARG CODEX_NPM_PACKAGE=@openai/codex
ARG OPENCODE_NPM_PACKAGE=opencode-ai

RUN test -n "$CLAUDE_VERSION" \
    && test -n "$CODEX_VERSION" \
    && test -n "$OPENCODE_VERSION" \
    && npm install -g \
      "${CLAUDE_NPM_PACKAGE}@${CLAUDE_VERSION}" \
      "${CODEX_NPM_PACKAGE}@${CODEX_VERSION}" \
      "${OPENCODE_NPM_PACKAGE}@${OPENCODE_VERSION}" \
    && claude --version \
    && codex --version \
    && opencode --version

FROM ${AI_REVIEW_BASE_IMAGE}

COPY --from=reviewer-clis /usr/local/bin/node /usr/local/bin/node
COPY --from=reviewer-clis /usr/local/bin/npm /usr/local/bin/npm
COPY --from=reviewer-clis /usr/local/bin/npx /usr/local/bin/npx
COPY --from=reviewer-clis /usr/local/bin/claude /usr/local/bin/claude
COPY --from=reviewer-clis /usr/local/bin/codex /usr/local/bin/codex
COPY --from=reviewer-clis /usr/local/bin/opencode /usr/local/bin/opencode
COPY --from=reviewer-clis /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN claude --version \
    && codex --version \
    && opencode --version

WORKDIR /workspace
