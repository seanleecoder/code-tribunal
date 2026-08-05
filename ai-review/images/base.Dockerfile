FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b

# Test-only packaging marker with no production runtime behavior; checkout-based tests must not override it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/ai-review/src \
    AI_REVIEW_PACKAGED_RUNTIME=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY ai-review/images/python-constraints.txt /opt/ai-review/images/python-constraints.txt

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir \
      --constraint /opt/ai-review/images/python-constraints.txt \
      jsonschema PyYAML requests

COPY ai-review/adapters /opt/ai-review/adapters
COPY ai-review/ci /opt/ai-review/ci
COPY ai-review/config /opt/ai-review/config
COPY ai-review/images /opt/ai-review/images
COPY ai-review/prompts /opt/ai-review/prompts
COPY ai-review/rules /opt/ai-review/rules
COPY ai-review/schemas /opt/ai-review/schemas
COPY ai-review/src/ai_review /opt/ai-review/src/ai_review
# Only the fixtures ship, and they are required: the reviewer preflight runs
# `docker run --read-only` with no mount and resolves --diff/--repo from here.
# Test *code* is staged in at verification time instead, so a production image that
# processes untrusted diffs and model output carries none of it, and a change to test
# code no longer alters image identity. Fixtures are the exception — they are a
# shipped layer, so changing one does change the image digest and is part of the
# release binding.
COPY ai-review/tests/fixtures /opt/ai-review/tests/fixtures
COPY scripts/check_supply_chain_pins.py /opt/scripts/check_supply_chain_pins.py
COPY scripts/smoke_cursor_permissions.sh /opt/scripts/smoke_cursor_permissions.sh
COPY scripts/smoke_opencode_search_tools.py /opt/scripts/smoke_opencode_search_tools.py
COPY scripts/smoke_opencode_search_tools.sh /opt/scripts/smoke_opencode_search_tools.sh
COPY README.md /opt/README.md
COPY ai-review/README.md /opt/ai-review/README.md

RUN chmod +x /opt/ai-review/adapters/*.sh \
    && python -m compileall -q /opt/ai-review/src

WORKDIR /workspace
