FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/ai-review/src \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY ai-review/images/python-constraints.txt /opt/ai-review/images/python-constraints.txt

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir \
      --constraint /opt/ai-review/images/python-constraints.txt \
      jsonschema PyYAML python-gitlab requests

COPY ai-review/adapters /opt/ai-review/adapters
COPY ai-review/ci /opt/ai-review/ci
COPY ai-review/config /opt/ai-review/config
COPY ai-review/images /opt/ai-review/images
COPY ai-review/prompts /opt/ai-review/prompts
COPY ai-review/rules /opt/ai-review/rules
COPY ai-review/schemas /opt/ai-review/schemas
COPY ai-review/src /opt/ai-review/src
COPY ai-review/tests /opt/ai-review/tests
COPY scripts/check_supply_chain_pins.py /opt/scripts/check_supply_chain_pins.py
COPY README.md /opt/README.md
COPY ai-review/README.md /opt/ai-review/README.md

RUN chmod +x /opt/ai-review/adapters/*.sh \
    && python -m compileall -q /opt/ai-review/src \
    && python -m unittest discover -s /opt/ai-review/tests -p 'test_*.py'

WORKDIR /workspace
