# Alpine edge, not a stable release, because it is the only base carrying a
# current fwupd (2.1.7 vs 2.0.20 in Debian stable and Alpine stable) while also
# being the smallest -- 331MB against Debian trixie's 503MB. musl costs almost
# nothing here: 144 fwupd plugins against Debian's 146, and every Python
# dependency ships musllinux wheels so nothing compiles from source.
#
# The trade-off is that edge is Alpine's rolling development branch: package
# versions move continuously and a rebuild months from now may resolve
# differently or fail. Rebuild deliberately, and run `make integration` after
# every base bump -- that suite is what catches a fwupd JSON change.
FROM alpine:edge

# uv provides dependency resolution; pinned rather than :latest so builds are reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Adds the fwupd-tests package, which provides synthetic devices for the
# integration suite. Off by default -- the shipped image carries no test
# fixtures. Build with --build-arg WITH_TEST_DEVICES=true to enable.
ARG WITH_TEST_DEVICES=false

# fwupd brings fwupdtool, which runs the engine in-process; no daemon, no DBus
# broker and no init system are ever started in this image.
# bash is needed by scripts/integration-test.sh, not by the application.
RUN apk add --no-cache \
        fwupd \
        ca-certificates \
        python3 \
        bash \
    && if [ "$WITH_TEST_DEVICES" = "true" ]; then \
           apk add --no-cache fwupd-tests; \
       fi

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

EXPOSE 8099

# Runs as root deliberately: device enumeration requires it.
CMD ["python", "-m", "fwupd_webui"]
