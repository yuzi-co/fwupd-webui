#!/usr/bin/env bash
# Capture real fwupdtool JSON into tests/fixtures/.
#
# Builds a throwaway image with the fwupd-tests package so fwupd's synthetic
# devices are available; the shipped image never carries them. This makes the
# capture reproducible on any machine, including CI and laptops with no
# interesting firmware.
#
# fwupdtool writes progress ("Loading?: 42%") to stderr and JSON to stdout, so
# every capture discards stderr.
#
# Run:  ./scripts/capture-fixtures.sh
set -euo pipefail

IMAGE="${IMAGE:-fwupd-webui:test}"
OUT="tests/fixtures"
mkdir -p "$OUT"

echo "==> building $IMAGE with synthetic test devices"
docker build --build-arg WITH_TEST_DEVICES=true -t "$IMAGE" . >/dev/null

run() {
    docker run --rm "$IMAGE" bash -c "$1" 2>/dev/null
}

echo "==> get-devices (with synthetic test devices)"
run 'fwupdtool enable-test-devices >/dev/null 2>&1; fwupdtool --json get-devices' \
    > "$OUT/get-devices.json"

echo "==> get-updates (with synthetic test devices)"
run 'fwupdtool enable-test-devices >/dev/null 2>&1; fwupdtool --json get-updates' \
    > "$OUT/get-updates.json"

echo "==> get-updates with no updatable devices"
run 'fwupdtool --json get-updates' > "$OUT/get-updates-empty.json"

echo "==> version"
run 'fwupdtool --json --version' > "$OUT/version.json"

echo
echo "Captured:"
wc -c "$OUT"/*.json
echo
python3 - "$OUT" <<'PY'
import json
import pathlib
import sys

for path in sorted(pathlib.Path(sys.argv[1]).glob("*.json")):
    payload = json.loads(path.read_text())
    devices = payload.get("Devices", [])
    print(f"{path}: keys={sorted(payload)} devices={len(devices)}")
    for device in devices:
        releases = [r.get("Version") for r in device.get("Releases", [])]
        print(f"    {device.get('Plugin')} | {device.get('Name')} | "
              f"version={device.get('Version')} | releases={releases}")
PY
