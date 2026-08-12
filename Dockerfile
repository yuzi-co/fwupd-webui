# Debian forky (testing), pinned to a snapshot.debian.org timestamp.
#
# forky carries fwupd 2.1.7 -- the current upstream release -- where Debian
# stable (trixie) is stuck on 2.0.20 and trixie-backports offers nothing newer.
# It is testing, not stable, so package versions do move; the snapshot pin below
# is what makes that irrelevant. An archive timestamp is immutable, so this
# Dockerfile resolves to identical packages on any future rebuild.
#
# Alpine edge also carries fwupd 2.1.7 and builds a 331MB image against this
# one's 472MB, but edge has no snapshot archive by construction -- its packages
# are replaced in place, so an edge build cannot be pinned at all. Reproducible
# rebuilds are worth 141MB here. musl also rules out every upstream Linux binary
# artifact, all of which are glibc-linked.
#
# To move fwupd forward, bump DEBIAN_SNAPSHOT to a newer timestamp deliberately,
# then run `make integration` -- that suite exercises real fwupdtool and is what
# catches a version bump that changes fwupd's JSON output.
ARG DEBIAN_SNAPSHOT=20260810T000000Z

FROM debian:forky-slim
ARG DEBIAN_SNAPSHOT

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

# Repoint apt at the immutable snapshot archive. Check-Valid-Until is disabled
# because a pinned Release file is stale by definition the day after the
# snapshot was taken; the archive is content-addressed and still signed, so the
# signature check that matters is unaffected.
RUN printf '%s\n' \
        'Types: deb' \
        "URIs: http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}" \
        'Suites: forky forky-updates' \
        'Components: main' \
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' \
        '' \
        'Types: deb' \
        "URIs: http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}" \
        'Suites: forky-security' \
        'Components: main' \
        'Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp' \
    > /etc/apt/sources.list.d/debian.sources \
    && echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/10no-check-valid-until

# fwupd brings fwupdtool, which runs the engine in-process; the daemon and its
# systemd units arrive in the same package but are never started -- this image
# has no init system.
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

EXPOSE 8099

# Runs as root deliberately: device enumeration requires it.
CMD ["python", "-m", "fwupd_webui"]
