# fwupd Web UI — Design

**Date:** 2026-08-11
**Status:** Approved
**Scope:** Phase C (read-only inventory). Phase A (flashing) is designed for but not built here.

## Problem

Unraid servers accumulate firmware — NVMe drives, LSI/HBA controllers, network cards, Thunderbolt
controllers, USB hubs, docks — and their owners have no visibility into what versions are running or
whether updates exist. Unraid ships no `fwupd`: the OS is Slackware, loaded into RAM from a USB
stick, with no persistent package manager. The natural distribution mechanism on that platform is a
Docker container installed through Community Applications.

The goal is a container that answers "what firmware is on this machine, and is any of it out of
date" without touching anything.

## Goals

- Enumerate every device `fwupd` can see on the host, with current firmware versions.
- Show available updates from LVFS metadata for those devices.
- Install and run cleanly on Unraid via a Community Applications template.
- Be structurally incapable of writing firmware in this phase.

## Non-Goals

Explicitly out of scope for v1, so they do not creep in during implementation:

- Flashing firmware of any kind (phase A).
- UEFI capsule / BIOS updates (deferred indefinitely; see Risks).
- Authentication — LAN-only, matching every other Unraid container.
- Scheduler, notifications, firmware-version history database (phase 2).
- Multi-host / fleet management.
- arm64 builds. Unraid is x86_64 only.

## Phasing

| Phase | Content | Status |
| --- | --- | --- |
| C | Read-only inventory + available updates | This spec |
| A | Flashing runtime-updatable devices (NVMe, HBA, NIC, Thunderbolt) — no reboot, no ESP | Next |
| 2 | Scheduler, history DB, update notifications | Later |
| B | UEFI capsule / BIOS updates | Probably never; see Risks |

## Architecture

A single container running a single Python process (uvicorn), serving FastAPI with Jinja2 templates
and htmx for interactivity. There is **no fwupd daemon, no DBus, and no systemd** inside the
container. The backend shells out to `fwupdtool --json`.

### Why daemonless

`fwupdtool` runs the fwupd engine in-process rather than talking to a system daemon over DBus. Its
subcommand set was verified directly against upstream source at tags 1.9.16, 2.0.0, 2.0.14, and
`main`; in every one of them `src/fu-tool.c` registers:

- `get-devices,get-topology`
- `get-updates,get-upgrades`
- `refresh`
- `install`
- `install-blob`

This means the entire read-only feature set — and phase A's write path — is reachable without a
daemon, DBus broker, or init system in the image. The container stays a single process.

### Base image and fwupd version

`alpine:edge`, with `fwupd` installed from apk. The resolved version is recorded in the image and
displayed in the UI header, so "which fwupd is this" is answerable from the running container.

**Amended 2026-08-12.** The original choice was `debian:trixie-slim`, which pins fwupd at 2.0.20.
Alpine edge was measured against the alternatives on real builds:

| Base | fwupd | Size | Plugins |
| --- | --- | --- | --- |
| `debian:trixie-slim` | 2.0.20 | 503 MB | 132 |
| `debian:forky-slim` | 2.1.7 | 519 MB | 146 |
| `alpine:edge` | 2.1.7 | 331 MB | 144 |
| `alpine:latest` | 2.0.20 | 328 MB | 131 |

Alpine edge wins on both axes at once — current fwupd and a third off the image — and musl costs
almost nothing: 144 plugins against Debian's 146, with musllinux wheels for every Python
dependency so nothing compiles from source.

The cost is reproducibility. Edge is a rolling development branch; package versions move
continuously and a rebuild months from now may resolve differently or fail outright. This is
accepted deliberately, on the reasoning that current device coverage is the whole point of an
inventory tool, and that the integration suite running against real `fwupdtool` is what catches a
base-image bump that breaks parsing. Run `make integration` after every rebuild.

The process runs as **root**. Device enumeration requires it. This is documented rather than
worked around.

### Host access

The container is useless without host device visibility:

| Mount / flag | Purpose |
| --- | --- |
| `/sys` | fwupd reads sysfs attributes |
| `/dev` | NVMe, SCSI generic, and MTD ioctls |
| `/run/udev` (read-only) | fwupd enumerates through the udev database |
| `--privileged` | v1 default in the Unraid template |

`/run/udev` is the non-obvious one. Without the host's udev database, fwupd's enumeration returns a
nearly empty device list rather than an error — a silent failure that looks like "this container is
broken". The empty-state screen (below) exists specifically to catch it.

Replacing `--privileged` with a narrower explicit capability set is a desirable later refinement,
not a v1 requirement.

### Read-only enforcement

Read-only is a structural property, not a convention:

1. The baked `/etc/fwupd/fwupd.conf` sets `DisabledPlugins=uefi_capsule`, so the plugin capable of
   staging a capsule to the ESP is not loaded at all.
2. No code path in the application constructs an `install` argv. `fwupd/cli.py` exposes only
   read verbs.

Phase A lifts item 2 deliberately and explicitly. Item 1 stays until phase B, if it ever happens.

## Modules

Each module has one responsibility and a boundary that can be tested independently.

### `fwupd/cli.py`

The only module in the codebase that imports `subprocess`. Builds argv, invokes `fwupdtool`, parses
JSON from stdout, and maps exit codes and stderr onto typed exceptions. Knows nothing about HTTP or
about the shape of the data beyond "it is JSON".

Exposes: `get_devices()`, `get_updates()`, `refresh()`, `version()`.

### `fwupd/models.py`

Pydantic models — `Device`, `Release`, `Remote` — mapping fwupd's JSON. Deliberately tolerant of
unknown fields so that a fwupd version bump adding keys does not break parsing. Missing *expected*
fields, by contrast, should fail loudly.

### `fwupd/service.py`

Orchestration. Joins `get-devices` output against `get-updates` output by device ID to produce the
view the UI wants: every device, annotated with whether an update exists and what releases are
available. Owns the metadata-refresh policy and the concurrency lock.

### `web/routes.py` and `web/templates/`

FastAPI routes returning either full pages or htmx fragments. Templates: a base layout, the device
table, the expandable device-detail partial, the error banner, and the empty state.

### `config.py`

Environment variable parsing. No config file.

## Metadata cache

LVFS metadata persists in a volume mounted at `/var/lib/fwupd`, so a container restart does not
force a network fetch before the UI becomes useful.

On startup, `fwupdtool refresh` runs only if the cached metadata is older than the configured
threshold (default 24 hours). The header carries a manual **Refresh** button for on-demand updates.

Refresh failure is never fatal. The device table still renders, with a banner showing the metadata
age and the reason the refresh failed. A machine with no internet access still gets a full
inventory — it simply cannot tell you about available updates.

## UI

One page, plus one diagnostic screen.

**Device table.** Columns: name, vendor, current version, update-available badge, plugin, device
flags. Sorted so devices with updates surface first.

**Device detail.** Expanding a row issues an htmx request returning a fragment: GUIDs, device ID,
and every available release with version, date, urgency, and changelog, plus a link to the device's
LVFS page.

**Header.** fwupd version, metadata cache age, Refresh button.

**Empty state.** When enumeration returns zero devices, the UI renders a diagnostic page rather
than an empty table: what `fwupdtool` actually returned, which of the required mounts are present
inside the container, and what to fix. Zero devices is the single most likely support issue for
this container and it is almost always a missing `/run/udev` mount or missing privileges. Treating
it as a first-class screen rather than an edge case is a deliberate choice.

## Error handling

No failure mode produces a 500. Each renders a banner with actionable detail.

| Failure | Behavior |
| --- | --- |
| `fwupdtool` missing or exits non-zero | Typed exception carrying exit code and stderr tail; UI shows the actual stderr line |
| Malformed JSON | Parse error surfaced with a raw snippet — this signals fwupd version drift and must be visible, not swallowed |
| Refresh fails (no network) | Non-fatal; stale-metadata banner with cache age |
| Subprocess hang | Hard timeout on every invocation, env-tunable |

### Concurrency

Two rules that matter more than they appear:

1. **`fwupdtool` is blocking and slow.** Enumeration touches real hardware and takes seconds. Every
   invocation runs in a threadpool; none run on the event loop.
2. **Concurrent invocations against the same hardware are unsafe.** A single asyncio lock serializes
   them. Overlapping requests wait for the in-flight enumeration rather than spawning a second one.

## Testing

fwupd provides synthetic devices via `fwupdtool enable-test-devices`, which makes the real
subprocess path testable without hardware.

**Unit.** `models.py` parses recorded `fwupdtool --json` fixtures committed to the repo. Fixtures
are captured from real hosts and include at least one messy case — many devices, unusual plugins,
missing optional fields.

**Integration.** CI runs the built image with test devices enabled and asserts the full
`cli.py` → `service.py` chain against genuine `fwupdtool` output. This is the test that catches a
fwupd version bump changing the JSON shape, which fixtures alone cannot.

**Web.** FastAPI `TestClient` against a faked service: the table renders, row expansion returns the
correct fragment, and an injected failure produces the error banner.

**Empty state.** An explicit test that zero devices yields the diagnostic page rather than a blank
table.

## Configuration

Environment variables only:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FWUPD_WEBUI_PORT` | `8080` | Listen port |
| `FWUPD_WEBUI_REFRESH_INTERVAL_HOURS` | `24` | Age above which startup triggers a metadata refresh |
| `FWUPD_WEBUI_TIMEOUT_SECONDS` | `120` | Hard timeout per `fwupdtool` invocation |
| `FWUPD_WEBUI_LVFS_REMOTE` | `lvfs` | `lvfs` or `lvfs-testing` |
| `FWUPD_WEBUI_LOG_LEVEL` | `info` | Log verbosity |

## Packaging

- Single-stage Dockerfile using `uv` for dependency installation.
- `docker-compose.yml` for local development, with host mounts wired so development runs against
  real hardware.
- **amd64 only.**
- Published to `ghcr.io`.
- An Unraid Community Applications XML template declaring the port, the three mounts, the
  privileged flag, the WebUI link, an icon, and project/support URLs.

## Risks

**Zero devices on a correctly configured host.** Possible if Unraid's kernel or udev layout differs
from what fwupd expects. Mitigated by the diagnostic empty state, which turns a silent failure into
a readable one. If it proves common, the fix is documentation, not code.

**fwupd version drift.** fwupd's JSON output is not a stability-guaranteed API. Mitigated by
pinning the version at build, tolerant parsing, and the integration test that runs real
`fwupdtool`.

**UEFI capsule updates (phase B) are dangerous on Unraid and are not planned.** A failed capsule
update bricks the motherboard. Worse, Unraid's ESP is the removable USB stick that holds the OS and
array configuration, and fwupd was not designed around that arrangement; mis-staging a capsule can
leave the stick unbootable. Recovery requires BIOS flashback or rebuilding the USB. The
`uefi_capsule` plugin is disabled in the image for this reason.

**Privileged container.** The template requests `--privileged`, which is a meaningful grant on a
NAS. Narrowing to an explicit capability set is tracked as a follow-up.

## Repository

`/home/dim/projects/fwupd-webui`, standalone git repository.
