# fwupd-webui

A read-only web UI for [fwupd](https://fwupd.org), packaged as a Docker container for
Unraid and other systems without a native fwupd installation.

It lists every device fwupd can enumerate — NVMe drives, HBAs, network cards,
Thunderbolt controllers, docks — with current firmware versions and any updates
available from LVFS.

**It does not flash firmware.** This release is inventory only.

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

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `FWUPD_WEBUI_PORT` | `8099` | Listen port |
| `FWUPD_WEBUI_REFRESH_INTERVAL_HOURS` | `24` | Age above which startup refetches LVFS metadata |
| `FWUPD_WEBUI_TIMEOUT_SECONDS` | `120` | Hard timeout per fwupdtool invocation |
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

### Why Alpine edge

The base is `alpine:edge` rather than a stable release, because it is the only base
that carries a current fwupd while also being the smallest. Measured on real builds:

| Base | fwupd | Size | Plugins |
| --- | --- | --- | --- |
| `debian:trixie-slim` | 2.0.20 | 503 MB | 132 |
| `debian:forky-slim` | 2.1.7 | 519 MB | 146 |
| **`alpine:edge`** | **2.1.7** | **331 MB** | **144** |
| `alpine:latest` | 2.0.20 | 328 MB | 131 |

musl costs almost nothing: 144 plugins against Debian's 146, and every Python
dependency ships musllinux wheels, so nothing compiles from source.

The trade-off is reproducibility. Edge is a rolling development branch — package
versions move continuously, and a rebuild months from now may resolve differently or
fail. **Run `make integration` after every rebuild**; that suite exercises real
`fwupdtool` and is what catches a base bump that changes fwupd's JSON.

The `uefi_capsule` plugin is disabled in the baked `/etc/fwupd/fwupd.conf`. That is
the plugin capable of staging a firmware capsule to the EFI system partition, which
on Unraid is the bootable USB stick holding the OS and array configuration. Disabling
it makes read-only a structural property rather than a convention, and an integration
test asserts the plugin reports itself disabled.

## Design documents

- [Design spec](docs/superpowers/specs/2026-08-11-fwupd-webui-design.md)
- [Phase C implementation plan](docs/superpowers/plans/2026-08-11-fwupd-webui-phase-c.md)

## License

MIT
