FROM debian:trixie-slim

# uv provides dependency resolution; pinned rather than :latest so builds are reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /bin/uv

ENV DEBIAN_FRONTEND=noninteractive \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Adds the fwupd-tests package, which provides synthetic devices for the
# integration suite. Off by default -- the shipped image carries no test
# fixtures. Build with --build-arg WITH_TEST_DEVICES=true to enable.
ARG WITH_TEST_DEVICES=false

# fwupd brings fwupdtool. The daemon and its systemd units come along in the same
# package but are never started -- this image has no init system.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fwupd \
        ca-certificates \
        python3 \
        python3-venv \
    && if [ "$WITH_TEST_DEVICES" = "true" ]; then \
           apt-get install -y --no-install-recommends fwupd-tests; \
       fi \
    && rm -rf /var/lib/apt/lists/*

COPY docker/fwupd.conf /etc/fwupd/fwupd.conf

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN uv venv /app/.venv && uv pip install --python /app/.venv/bin/python .

ENV PATH="/app/.venv/bin:${PATH}"

# Record the fwupd version this image was built against, so drift is diagnosable
# from `docker inspect` without starting the container.
RUN fwupdtool --version --json > /app/fwupd-version.json || \
    fwupdtool --version > /app/fwupd-version.txt

EXPOSE 8080

# Runs as root deliberately: device enumeration requires it.
CMD ["python", "-m", "fwupd_webui"]
