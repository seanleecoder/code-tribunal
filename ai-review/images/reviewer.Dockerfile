ARG AI_REVIEW_BASE_IMAGE=python:3.14.7-slim-trixie@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2
# Codex's native package directory, shared by the builder prune and the final-stage shim.
ARG CODEX_VENDOR=@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl
FROM node:26.10.0-trixie-slim@sha256:ec7758ee051e457b468b32bde57b0879010b325bb9862718e9615225ce4aaae1 AS reviewer-clis
ARG CODEX_VENDOR

WORKDIR /opt/ai-review/reviewer-clis
COPY ai-review/images/package.json ai-review/images/package-lock.json ./

# Install and prune in one layer: pruning in a later layer makes the final COPY
# materialise the postinstall hardlinks (claude.exe, opencode.exe) as second copies.
# After postinstall both are self-contained native binaries, so the platform packages
# they were linked from, and OpenCode's second x64 variant, are redundant. Codex's
# code-mode host and voice runtime back features `codex exec` leaves off; the feature
# check keeps the host removal fail-closed if a pin turns code mode on. The final
# stage's native-bin check and --version probes prove the pruned CLIs still run.
RUN set -eu; \
    npm ci --omit=dev; \
    npm cache clean --force; \
    nm=node_modules; \
    cx="$nm/$CODEX_VENDOR"; \
    rm -r "$nm"/@anthropic-ai/claude-code-linux-* "$nm"/opencode-linux-*; \
    CODEX_HOME="$(mktemp -d)" "$cx/bin/codex" features list \
      | grep -Eq '^code_mode[[:space:]].*[[:space:]]false$'; \
    rm "$cx/bin/codex-code-mode-host"; \
    rm -r "$cx/codex-resources/voice"

# Deliberately parallel to the ripgrep-bin stage below (same pin contract: presence
# checks, placeholder rejection, pinned URL, checksum, extract). The two stay
# duplicated rather than sharing a base stage so a change to one pinned artifact's
# verification cannot silently alter the other's.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a AS cursor-cli

WORKDIR /opt/cursor-agent-src
COPY ai-review/images/cursor-agent.pin ./cursor-agent.pin
RUN set -eu; \
    . ./cursor-agent.pin; \
    test -n "$version"; test -n "$url"; test -n "$sha256"; \
    if [ "$sha256" = "0000000000000000000000000000000000000000000000000000000000000000" ]; then \
      echo "cursor-agent.pin must be refreshed with the artifact sha256 before building" >&2; exit 1; \
    fi; \
    apt-get update; apt-get install -y --no-install-recommends ca-certificates curl tar; rm -rf /var/lib/apt/lists/*; \
    curl -fL "$url" -o cursor-agent.tar.gz; \
    echo "$sha256  cursor-agent.tar.gz" | sha256sum -c -; \
    mkdir -p /usr/local/cursor-agent; \
    tar -xzf cursor-agent.tar.gz -C /usr/local/cursor-agent --strip-components=1; \
    find /usr/local/cursor-agent -type f -name cursor-agent -exec chmod 0755 {} \; ; \
    test -x /usr/local/cursor-agent/cursor-agent || find /usr/local/cursor-agent -maxdepth 3 -type f -perm /111 -print -quit | xargs -r -I{} ln -sf {} /usr/local/cursor-agent/cursor-agent

# The verified package also carries two Node single-executable copies of the CLI
# (~283 MB) that only macOS `worker --computer-use` re-execs for code signing. The
# launcher runs the bundled node + index.js. Prune them only while the sole
# reference stays in the darwin-gated signed-CLI chunk, so a pin that starts using
# them on Linux fails here instead of at review time.
RUN set -eu; \
    cd /usr/local/cursor-agent; \
    refs="$(grep -rlF -e cursor-agent-sea -e cursor-agent-worker-sea \
      --exclude=cursor-agent-sea --exclude=cursor-agent-worker-sea . || true)"; \
    set -- $refs; \
    if [ "$#" -ne 1 ] \
      || ! grep -qF 'exports.modules={"./src/commands/worker-signed-cli.ts"' "$1" \
      || ! grep -qF '"darwin"!==' "$1"; then \
      echo "cursor-agent SEA referenced outside the darwin signed-CLI chunk: $refs" >&2; exit 1; \
    fi; \
    rm cursor-agent-sea cursor-agent-worker-sea

# Deliberately parallel to the cursor-cli stage above; see the comment there for why
# the verification bodies are not shared.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a AS ripgrep-bin

WORKDIR /opt/ripgrep-src
COPY ai-review/images/ripgrep.pin ./ripgrep.pin
RUN set -eu; \
    . ./ripgrep.pin; \
    test -n "$version"; test -n "$url"; test -n "$sha256"; test -n "$binary_sha256"; \
    if [ "$sha256" = "0000000000000000000000000000000000000000000000000000000000000000" ]; then \
      echo "ripgrep.pin must be refreshed with the artifact sha256 before building" >&2; exit 1; \
    fi; \
    if [ "$binary_sha256" = "0000000000000000000000000000000000000000000000000000000000000000" ]; then \
      echo "ripgrep.pin must be refreshed with the binary_sha256 before building" >&2; exit 1; \
    fi; \
    apt-get update; apt-get install -y --no-install-recommends ca-certificates curl tar; rm -rf /var/lib/apt/lists/*; \
    curl -fL "$url" -o ripgrep.tar.gz; \
    echo "$sha256  ripgrep.tar.gz" | sha256sum -c -; \
    mkdir -p /opt/ripgrep; \
    tar -xzf ripgrep.tar.gz -C /opt/ripgrep --strip-components=1; \
    test -f /opt/ripgrep/rg; \
    chmod 0755 /opt/ripgrep/rg; \
    echo "$binary_sha256  /opt/ripgrep/rg" | sha256sum -c -; \
    /opt/ripgrep/rg --version

FROM ${AI_REVIEW_BASE_IMAGE}

ARG CLAUDE_NPM_PACKAGE=@anthropic-ai/claude-code
ARG CODEX_NPM_PACKAGE=@openai/codex
ARG OPENCODE_NPM_PACKAGE=opencode-ai
ARG CODEX_VENDOR

# No Node runtime ships: claude and opencode are native executables, codex is
# launched natively below, and cursor-agent runs its own bundled node.
COPY --from=reviewer-clis /opt/ai-review/reviewer-clis/node_modules /usr/local/lib/node_modules
COPY --from=cursor-cli /usr/local/cursor-agent /usr/local/cursor-agent
COPY --from=ripgrep-bin /opt/ripgrep/rg /usr/local/bin/rg

RUN ln -sf /usr/local/cursor-agent/cursor-agent /usr/local/bin/cursor-agent \
    && if [ ! -e /usr/local/bin/agent ]; then ln -sf /usr/local/cursor-agent/cursor-agent /usr/local/bin/agent; fi

# Link each CLI's declared npm bin, refusing any target that is not a native ELF
# executable: with no node in the image, a JS entry point would be a broken CLI.
# Codex's declared bin is the codex.js launcher, so it becomes an exec shim that sets
# the environment codex.js sets and runs the entrypoint codex-package.json declares.
RUN set -eu; \
    nm=/usr/local/lib/node_modules; \
    json_get() { python3 -c 'import functools, json, sys; print(functools.reduce(lambda v, k: v[k], sys.argv[2:], json.load(open(sys.argv[1], encoding="utf-8"))))' "$@"; }; \
    require_elf() { test "$(head -c 4 "$1" | od -An -tx1 | tr -d ' \n')" = 7f454c46 \
      || { echo "$1 is not a native executable" >&2; exit 1; }; }; \
    link_native() { \
      target="$(json_get "$nm/$1/package.json" bin "$2")"; \
      path="$1/${target#./}"; \
      require_elf "$nm/$path"; \
      chmod 0755 "$nm/$path"; \
      ln -sfn "../lib/node_modules/$path" "/usr/local/bin/$2"; \
    }; \
    link_native "$CLAUDE_NPM_PACKAGE" claude; \
    link_native "$OPENCODE_NPM_PACKAGE" opencode; \
    cx="$nm/$CODEX_VENDOR"; \
    entry="$cx/$(json_get "$cx/codex-package.json" entrypoint)"; \
    require_elf "$entry"; \
    printf '%s\n' '#!/bin/sh' \
      'unset CODEX_MANAGED_BY_BUN CODEX_MANAGED_BY_PNPM CODEX_MANAGED_BY_VITE_PLUS' \
      "CODEX_MANAGED_BY_NPM=1 CODEX_MANAGED_PACKAGE_ROOT=$nm/$CODEX_NPM_PACKAGE" \
      'export CODEX_MANAGED_BY_NPM CODEX_MANAGED_PACKAGE_ROOT' \
      "exec $entry \"\$@\"" > /usr/local/bin/codex; \
    chmod 0755 /usr/local/bin/codex; \
    if command -v node >/dev/null; then echo "reviewer image must not ship node" >&2; exit 1; fi

# OpenCode's grep/glob tools shell out to ripgrep, resolving which("rg") first and
# otherwise downloading an unverified copy from GitHub at review time. Prove the
# pinned binary is the one that will be found, on the PATH the adapter forwards —
# not merely present somewhere in the image. The binary digest is re-checked here
# rather than only in the builder stage, because what matters is the identity of
# the file that ended up on PATH after the COPY, not what was downloaded.
RUN set -eu; \
    resolved="$(env -i PATH=/usr/local/bin:/usr/bin:/bin sh -c 'command -v rg')"; \
    test "$resolved" = "/usr/local/bin/rg"; \
    . /opt/ai-review/images/ripgrep.pin; \
    echo "$binary_sha256  /usr/local/bin/rg" | sha256sum -c -; \
    rg --version | grep -F -- "ripgrep $version"

# Build-time probes share one RUN so a single pair of tmpfs mounts keeps the CLIs'
# first-run state (install IDs, configs, logs, caches) out of every image layer.
RUN --mount=type=tmpfs,target=/root --mount=type=tmpfs,target=/tmp set -eu; \
    claude --version; \
    codex --version; \
    opencode --version; \
    cursor-agent --version; \
    # The adapter's read-only boundary depends on this pinned CLI surface. Fail the
    # image build if a future pin drops or renames native ask mode.
    cursor-agent --help | grep -F -- '--mode <mode>'; \
    # The OpenCode adapter uses the loopback server API and a static session title to
    # prevent a separate title-inference model call. Keep the pinned server surface
    # fail-closed if either required listen flag changes.
    serve_help="$(opencode --pure serve --help 2>&1 || true)"; \
    printf '%s\n' "$serve_help" | grep -F -- '--hostname'; \
    printf '%s\n' "$serve_help" | grep -F -- '--port'; \
    # Fail the image build if the pinned CLI ever rejects either of the claude
    # adapter's stage flag sets (claude.sh) — the review probe (finding schema,
    # --add-dir, repo tools) and the critique probe (critique schema, --tools "",
    # no --add-dir) — including the undocumented --output-format stream-json +
    # --json-schema interaction, probed with the real schemas shipped in the base
    # image (with the $schema draft key stripped, exactly as the adapter passes
    # them). No credentials exist at build time: an *accepted* argv still runs
    # far enough to emit a '"type":"result"' stream event (auth error), while a
    # *rejected* flag or schema prints an error on stderr and produces no stream
    # at all — so grep stdout for the result event and ignore the CLI's exit
    # code. This validates flag/schema acceptance and stream shape only; whether
    # structured_output is actually emitted is observable per run via the
    # runner's "ai-review: ..." job-log line.
    (echo probe | claude -p --safe-mode --model claude-haiku-4.5 \
      --no-session-persistence --output-format stream-json --verbose \
      --json-schema "$(python3 -c 'import json; s = json.load(open("/opt/ai-review/schemas/raw_finding_batch.schema.json", encoding="utf-8")); s.pop("$schema", None); print(json.dumps(s))')" \
      --add-dir /workspace --tools "Read,Grep,Glob" \
      --effort medium || true) \
      | grep -q '"type":"result"'; \
    (echo probe | claude -p --safe-mode --model claude-haiku-4.5 \
      --no-session-persistence --output-format stream-json --verbose \
      --json-schema "$(python3 -c 'import json; s = json.load(open("/opt/ai-review/schemas/critique_batch.schema.json", encoding="utf-8")); s.pop("$schema", None); print(json.dumps(s))')" \
      --tools "" \
      --effort medium || true) \
      | grep -q '"type":"result"'

WORKDIR /workspace
