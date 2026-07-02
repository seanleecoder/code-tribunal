FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/ai-review/src \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir \
      "jsonschema>=4.22" \
      "PyYAML>=6.0" \
      "python-gitlab>=4.7" \
      "requests>=2.32"

COPY ai-review/adapters /opt/ai-review/adapters
COPY ai-review/ci /opt/ai-review/ci
COPY ai-review/config /opt/ai-review/config
COPY ai-review/prompts /opt/ai-review/prompts
COPY ai-review/rules /opt/ai-review/rules
COPY ai-review/schemas /opt/ai-review/schemas
COPY ai-review/src /opt/ai-review/src
COPY ai-review/tests /opt/ai-review/tests
COPY ai-review/README.md /opt/ai-review/README.md

RUN chmod +x /opt/ai-review/adapters/*.sh \
    && python -m compileall -q /opt/ai-review/src \
    && python -m unittest discover -s /opt/ai-review/tests -p 'test_*.py'

WORKDIR /workspace
