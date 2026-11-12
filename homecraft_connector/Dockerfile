# BUILD_ARCH and BUILD_VERSION come from home-assistant/builder; dev compose
# passes BUILD_ARCH itself, a plain `docker build` gets amd64.
ARG BUILD_ARCH=amd64
FROM ghcr.io/home-assistant/${BUILD_ARCH}-base-python:3.12-alpine3.22-2026.10.3

# s6-overlay starts CMD with an empty environment otherwise: no SUPERVISOR_TOKEN
# (set by the Supervisor) and no dev overrides.
ENV S6_KEEP_ENV=1

# The version the agent reports in its hello.
ARG BUILD_VERSION=dev
ENV CONNECTOR_VERSION=$BUILD_VERSION

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY connector ./connector

CMD ["python3", "-m", "connector"]
