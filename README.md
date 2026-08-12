# fwupd-webui

A read-only web UI for [fwupd](https://fwupd.org), packaged as a Docker container for
Unraid and other systems without a native fwupd installation.

It lists every device fwupd can enumerate — NVMe drives, HBAs, network cards,
Thunderbolt controllers, docks — with current firmware versions and any updates
available from LVFS.

**Flashing is off by default.** Out of the box this is a read-only inventory. Setting
`FWUPD_WEBUI_ENABLE_FLASHING=true` enables the firmware write path; without it the flash
routes are not registered at all.

## Why a container

Unraid runs Slackware from a USB stick with the OS in RAM and no persistent package
manager, so there is no practical way to install fwupd on the host. A container is
the natural distribution mechanism on that platform.

## Running it

```bash
docker run -d --name fwupd-webui \
  --privileged \
  -p 8099:8099 \
  -v /sys:/sys \
  -v /dev:/dev \
  -v /run/udev:/run/udev:ro \
  -v /mnt/user/appdata/fwupd-webui:/var/lib/fwupd \
  ghcr.io/yuzi-co/fwupd-webui:latest
```

On Unraid, install from Community Applications instead — the template sets all of
this up for you.

### Why privileged

Enumerating firmware means talking to hardware: NVMe admin commands, SCSI generic
ioctls, sysfs attributes. That needs privileges a normal container does not have.
Narrowing this to an explicit capability set is a planned improvement.

### Flashing firmware

Disabled unless `FWUPD_WEBUI_ENABLE_FLASHING=true`. When it is off the routes do not
exist — `POST /flash` is a genuine 404, not a handler that refuses.

With it on, four things still stand between a click and a write:

1. **Nothing flashes without typing the device name exactly.** There is no
   allowlist and no one-click path. Storage devices (`nvme`, `ata`, `scsi`, `emmc`)
   additionally show a prominent data-loss warning.
2. Policy is enforced server-side. A disabled button is a suggestion; the server
   refusing the POST is the control.
3. There is no cancel. Killing a flash mid-write can leave partially written
   firmware, so a job runs to completion or fails on its own.
4. `uefi_capsule` remains disabled in the image, so BIOS updates stay unreachable.

**Storage devices.** The hazard is not which plugin drives a disk, it is that the disk
holds data something is using. On a NAS the `ata` devices are usually the array and the
`nvme` is usually the cache pool — which is where Docker itself lives. All storage is
therefore treated as the dangerous case regardless of plugin. Stop the array or unmount
the device before flashing it.

**Staged firmware.** Most devices report `needs-reboot`, meaning the firmware is
written but becomes live only after a reboot. The result screen says which happened,
by re-reading the device after the flash. This tool never reboots the host.

**Leaving it on.** Nothing expires the flag. Turning it off again is a container
restart, and that is the only thing keeping the outermost control meaningful.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `FWUPD_WEBUI_PORT` | `8099` | Listen port |
| `FWUPD_WEBUI_ENABLE_FLASHING` | `false` | Enables the firmware write path |
| `FWUPD_WEBUI_REFRESH_INTERVAL_HOURS` | `24` | Age above which startup refetches LVFS metadata |
| `FWUPD_WEBUI_TIMEOUT_SECONDS` | `120` | Hard timeout per fwupdtool invocation |
| `FWUPD_WEBUI_INSTALL_TIMEOUT_SECONDS` | `1800` | Hard timeout for a single flash |
| `FWUPD_WEBUI_LVFS_REMOTE` | `lvfs` | `lvfs` or `lvfs-testing` |
| `FWUPD_WEBUI_LOG_LEVEL` | `info` | Log verbosity |

## Troubleshooting

**No devices listed.** The UI shows a diagnostic page for this case listing which
required mounts are visible. The usual cause is a missing `/run/udev` mount — fwupd
enumerates through the udev database and returns an empty list without it, rather
than an error.

**Metadata is stale.** Refresh failures are non-fatal; the device inventory still
renders. Check that the container can reach `fwupd.org`.

## Development

```bash
make install      # sync dependencies
make test         # unit tests
make lint         # ruff
make image        # build the container
make fixtures     # regenerate test fixtures from real fwupdtool output
make integration  # run tests against real fwupdtool inside the image
```

`make fixtures` and `make integration` build the image with
`--build-arg WITH_TEST_DEVICES=true`, which adds the `fwupd-tests` package so
fwupd's synthetic devices are available. The shipped image never includes it.

## Architecture

There is no fwupd daemon in this container. The backend shells out to `fwupdtool`,
which runs the fwupd engine in-process — so the image needs no DBus broker and no
init system, and stays a single Python process.

### Why Debian forky, pinned to a snapshot

The base is `debian:forky-slim` pinned to a `snapshot.debian.org` timestamp. Debian
stable (trixie) carries fwupd 2.0.20 and `trixie-backports` offers nothing newer, so
a current fwupd means leaving stable. Measured on real builds:

| Base | fwupd | Size | Plugins |
| --- | --- | --- | --- |
| `debian:trixie-slim` | 2.0.20 | 503 MB | 132 |
| **`debian:forky-slim`** | **2.1.7** | **472 MB** | **146** |
| `alpine:edge` | 2.1.7 | 331 MB | 144 |
| `alpine:latest` | 2.0.20 | 328 MB | 131 |

Alpine edge reaches the same fwupd in a third less space, but edge has no snapshot
archive — its packages are replaced in place, so an edge build cannot be pinned at
all. forky is testing rather than stable, and its package versions do move, but
pinning an immutable archive timestamp makes that irrelevant: this Dockerfile
resolves to identical packages on any future rebuild. Reproducibility is worth the
141 MB here, and glibc keeps every upstream Linux binary artifact usable, all of
which are glibc-linked.

Two consequences worth knowing:

- **Security updates arrive only when you bump the pin.** That is the cost of a
  frozen archive, and it matters more than usual because the container runs
  privileged. Debian's security team also covers testing more thinly than stable.
- **Bump `DEBIAN_SNAPSHOT` deliberately, then run `make integration`.** That suite
  exercises real `fwupdtool` and is what catches a version bump that changes fwupd's
  JSON output.

```bash
docker build --build-arg DEBIAN_SNAPSHOT=20260810T000000Z -t fwupd-webui:dev .
```

Building fwupd from source was considered and rejected: upstream publishes no Linux
binary (releases carry a Windows MSI and a source tarball), git `main` past 2.1.7 is
158 commits of internal refactor with one new device driver, and pinning a fwupd
commit would still leave its twenty-odd runtime dependencies unpinned.

The `uefi_capsule` plugin is disabled in the baked `/etc/fwupd/fwupd.conf`. That is
the plugin capable of staging a firmware capsule to the EFI system partition, which
on Unraid is the bootable USB stick holding the OS and array configuration. Disabling
it makes read-only a structural property rather than a convention, and an integration
test asserts the plugin reports itself disabled.

## Design documents

- [Phase C design — read-only inventory](docs/specs/2026-08-11-fwupd-webui-design.md)
- [Phase C implementation plan](docs/plans/2026-08-11-fwupd-webui-phase-c.md)
- [Phase A design — flashing](docs/specs/2026-08-12-fwupd-webui-phase-a-design.md)

## License

MIT
