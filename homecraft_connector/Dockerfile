# BUILD_ARCH comes from home-assistant/builder (amd64 | aarch64); a plain
# `docker build` (dev compose) gets the host's default below.
ARG BUILD_ARCH=amd64
FROM ghcr.io/home-assistant/${BUILD_ARCH}-base-python:3.12-alpine3.22-2026.10.3

# s6-overlay starts CMD with an empty environment otherwise: no SUPERVISOR_TOKEN
# (set by the Supervisor) and no dev overrides.
ENV S6_KEEP_ENV=1

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# config.yaml carries the version the agent reports in its hello.
COPY config.yaml .
COPY connector ./connector

CMD ["python3", "-m", "connector"]
