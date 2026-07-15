ARG AI_REVIEW_BASE_IMAGE=python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b
FROM node:22-bookworm-slim@sha256:53ada149d435c38b14476cb57e4a7da73c15595aba79bd6971b547ceb6d018bf AS reviewer-clis

WORKDIR /opt/ai-review/reviewer-clis
COPY ai-review/images/package.json ai-review/images/package-lock.json ./

RUN npm ci --omit=dev \
    && npm cache clean --force \
    && ./node_modules/.bin/claude --version \
    && ./node_modules/.bin/codex --version \
    && ./node_modules/.bin/opencode --version

FROM debian:bookworm-slim@sha256:df52e55e3361a81ac1bead266f3373ee55d29aa50cf0975d440c2be3483d8ed3 AS cursor-cli

WORKDIR /opt/cursor-agent-src
COPY ai-review/images/cursor-agent.pin ./cursor-agent.pin
RUN set -eu; \
    . ./cursor-agent.pin; \
    test -n "$version"; test -n "$url"; test -n "$sha256"; \
    if [ "$sha256" = "TO_BE_REFRESHED_WITH_ACCESSIBLE_CURSOR_ARTIFACT_SHA256" ]; then \
      echo "cursor-agent.pin must be refreshed with the artifact sha256 before building" >&2; exit 1; \
    fi; \
    apt-get update; apt-get install -y --no-install-recommends ca-certificates curl tar; rm -rf /var/lib/apt/lists/*; \
    curl -fL "$url" -o cursor-agent.tar.gz; \
    echo "$sha256  cursor-agent.tar.gz" | sha256sum -c -; \
    mkdir -p /usr/local/cursor-agent; \
    tar -xzf cursor-agent.tar.gz -C /usr/local/cursor-agent --strip-components=1; \
    find /usr/local/cursor-agent -type f -name cursor-agent -exec chmod 0755 {} \; ; \
    test -x /usr/local/cursor-agent/cursor-agent || find /usr/local/cursor-agent -type f -perm /111 -maxdepth 3 -print -quit | xargs -r -I{} ln -sf {} /usr/local/cursor-agent/cursor-agent

FROM ${AI_REVIEW_BASE_IMAGE}

ARG CLAUDE_NPM_PACKAGE=@anthropic-ai/claude-code
ARG CODEX_NPM_PACKAGE=@openai/codex
ARG OPENCODE_NPM_PACKAGE=opencode-ai

COPY --from=reviewer-clis /usr/local/bin/node /usr/local/bin/node
COPY --from=reviewer-clis /usr/local/bin/npm /usr/local/bin/npm
COPY --from=reviewer-clis /usr/local/bin/npx /usr/local/bin/npx
COPY --from=reviewer-clis /opt/ai-review/reviewer-clis/node_modules /usr/local/lib/node_modules
COPY --from=cursor-cli /usr/local/cursor-agent /usr/local/cursor-agent

RUN ln -sf /usr/local/cursor-agent/cursor-agent /usr/local/bin/cursor-agent \
    && if [ ! -e /usr/local/bin/agent ]; then ln -sf /usr/local/cursor-agent/cursor-agent /usr/local/bin/agent; fi

RUN node -e 'const fs = require("fs"); \
const path = require("path"); \
for (const packageName of process.argv.slice(1)) { \
  const packageRoot = path.join("/usr/local/lib/node_modules", packageName); \
  const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, "package.json"), "utf8")); \
  const bin = manifest.bin || {}; \
  const entries = typeof bin === "string" ? [[manifest.name.replace(/^@[^/]+\//, ""), bin]] : Object.entries(bin); \
  for (const [name, target] of entries) { \
    if (!name || /[\\/]/.test(name)) { \
      throw new Error(`Invalid npm bin name for ${packageName}: ${name}`); \
    } \
    const targetPath = path.join(packageRoot, target); \
    const link = path.join("/usr/local/bin", name); \
    const relativeTarget = path.relative(path.dirname(link), targetPath); \
    try { \
      const stat = fs.lstatSync(link); \
      if (stat.isDirectory()) { \
        throw new Error(`Refusing to replace directory: ${link}`); \
      } \
      fs.unlinkSync(link); \
    } catch (error) { \
      if (error.code !== "ENOENT") { \
        throw error; \
      } \
    } \
    fs.chmodSync(targetPath, 0o755); \
    fs.symlinkSync(relativeTarget, link); \
  } \
}' "$CLAUDE_NPM_PACKAGE" "$CODEX_NPM_PACKAGE" "$OPENCODE_NPM_PACKAGE"

RUN claude --version \
    && codex --version \
    && opencode --version \
    && cursor-agent --version

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
RUN (echo probe | claude -p --safe-mode --model claude-haiku-4.5 \
      --no-session-persistence --output-format stream-json --verbose \
      --json-schema "$(python3 -c 'import json; s = json.load(open("/opt/ai-review/schemas/raw_finding_batch.schema.json", encoding="utf-8")); s.pop("$schema", None); print(json.dumps(s))')" \
      --bare --add-dir /workspace --tools "Read,Grep,Glob" \
      --effort medium || true) \
    | grep -q '"type":"result"' \
    && (echo probe | claude -p --safe-mode --model claude-haiku-4.5 \
      --no-session-persistence --output-format stream-json --verbose \
      --json-schema "$(python3 -c 'import json; s = json.load(open("/opt/ai-review/schemas/critique_batch.schema.json", encoding="utf-8")); s.pop("$schema", None); print(json.dumps(s))')" \
      --bare --tools "" \
      --effort medium || true) \
    | grep -q '"type":"result"'

WORKDIR /workspace
