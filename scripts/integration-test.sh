#!/usr/bin/env bash
# Run the integration suite inside the built image, against real fwupdtool.
#
# Builds with WITH_TEST_DEVICES=true so fwupd's synthetic devices exist. The
# image's virtualenv is created by `uv venv` and has no pip, so test
# dependencies are installed with uv.
set -euo pipefail

IMAGE="${IMAGE:-fwupd-webui:test}"

echo "==> building $IMAGE with synthetic test devices"
docker build --build-arg WITH_TEST_DEVICES=true -t "$IMAGE" . >/dev/null

docker run --rm \
    -v "$PWD/tests:/app/tests:ro" \
    "$IMAGE" \
    bash -c 'uv pip install --quiet --python /app/.venv/bin/python \
                 pytest pytest-asyncio httpx && \
             cd /app && python -m pytest tests/integration -v -p no:cacheprovider'
