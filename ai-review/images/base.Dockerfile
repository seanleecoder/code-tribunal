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
# Only the fixtures ship from the test tree, and they are required: the reviewer
# preflight runs `docker run --read-only` with no mount and resolves --diff/--repo
# from here, and the packaged smoke suite asserts these exact paths. The checkout
# suite's test_*.py modules are never copied, so a production image that processes
# untrusted diffs and model output carries no product test code. Fixtures are a
# shipped layer, so changing one does change the image digest and is part of the
# release binding.
COPY ai-review/tests/fixtures /opt/ai-review/tests/fixtures
COPY scripts/check_supply_chain_pins.py /opt/scripts/check_supply_chain_pins.py
COPY scripts/smoke_opencode_search_tools.py /opt/scripts/smoke_opencode_search_tools.py
COPY scripts/smoke_opencode_search_tools.sh /opt/scripts/smoke_opencode_search_tools.sh
COPY scripts/smoke_opencode_structured_output.py /opt/scripts/smoke_opencode_structured_output.py
COPY scripts/smoke_opencode_structured_output.sh /opt/scripts/smoke_opencode_structured_output.sh
COPY README.md /opt/README.md
COPY ai-review/README.md /opt/ai-review/README.md

# The curated packaged-runtime smoke suite, and the single deliberate exception to
# "runtime images carry no test code". It is stdlib-only, imports nothing from the
# checkout suite, and never runs during this build — only at preflight, so smoke
# test changes still do not alter image identity. The COPY is deliberately this
# narrow: it is what restores the build-time guarantee the removed executed-test
# floor was compensating for (a renamed or deleted suite fails the build here
# instead of passing vacuously against an empty bind mount), while a revert to
# copying the whole test tree still fails the distribution contract. It is the
# last COPY because it is the layer most likely to change on its own, and every
# preceding one stays cached when it does.
COPY ai-review/src/ai_review_smoke /opt/ai-review/src/ai_review_smoke

RUN chmod +x /opt/ai-review/adapters/*.sh \
    && python -m compileall -q /opt/ai-review/src

WORKDIR /workspace
