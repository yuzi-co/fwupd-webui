# Unraid Community Applications — submission draft

CA does not host templates. It indexes template *repositories* and scrapes the XML from
them, so nothing needs moving: `unraid/fwupd-webui.xml` stays where it is and the
repository is what gets registered. Once listed, CA picks up every template in the repo
automatically and re-scrapes on a schedule.

Registration is a request to the CA maintainers, posted in the **Community Applications
support thread on the Unraid forums** (forums.unraid.net). It is not a pull request.

Post this from your own forum account:

---

**Subject / opening line:** Request to add a template repository — fwupd-webui

Hi, could I have the following repository added to Community Applications?

**Repository:** https://github.com/yuzi-co/fwupd-webui
**Template:** `unraid/fwupd-webui.xml`
**Container:** `ghcr.io/yuzi-co/fwupd-webui` (public, amd64)

**What it does.** fwupd-webui is a web UI for [fwupd](https://fwupd.org). It lists every
device fwupd can enumerate on the server — NVMe drives, SATA disks, HBAs, network cards,
Thunderbolt controllers, docks — with current firmware versions and any updates available
from LVFS. Unraid ships no fwupd and has no persistent package manager, so a container is
the only practical way to get this on the platform.

**It is read-only by default.** Firmware flashing is disabled unless the operator sets
`FWUPD_WEBUI_ENABLE_FLASHING=true`; while it is off, the flash routes are not registered
in the application at all. Given this is a NAS catalogue, the safety model is worth
stating up front:

- Every flash requires typing the device name to confirm. There is no one-click path for
  any device.
- Storage devices — `nvme`, `ata`, `scsi`, `emmc` — additionally show a data-loss warning.
  On a NAS the `ata` devices are usually the array and the `nvme` is usually the cache
  pool, so all storage is treated as the dangerous case.
- System firmware is refused outright: `uefi_capsule`, `uefi_dbx` and `mtd` are never
  flashable. On Unraid the ESP is the removable USB stick holding the OS and array
  config, and a mis-staged capsule leaves it unbootable, so the tool simply does not go
  there.
- There is no cancel button. Killing a flash mid-write can leave partially written
  firmware, so a job runs to completion or fails on its own.

**It requires privileged mode**, and the template declares it. Enumerating firmware means
NVMe admin commands and SCSI generic ioctls; measured on a real Unraid host, a privileged
container finds 8 devices where every capability short of privileged finds 2 (the CPU and
the display). I tried to narrow it and could not, so the template is honest about needing
it rather than quietly requesting it.

**Details:**

- Project: https://github.com/yuzi-co/fwupd-webui
- Support: https://github.com/yuzi-co/fwupd-webui/issues
- Licence: MIT
- Registry: ghcr.io, public, no credentials required
- Category: Tools: Utilities
- WebUI: port 8099
- Tested on Unraid with the array running; screenshots are in the repository README.

Happy to adjust the template if anything in it does not meet CA conventions.

---

## Checklist before posting

- [ ] The GHCR package is public — verified by pulling anonymously with `docker logout`.
- [ ] `unraid/icon.png` resolves over raw.githubusercontent.
- [ ] The template's `Overview`, `Support`, `Project`, `Icon`, `Category` and `WebUI`
      fields are populated and accurate.
- [ ] `Privileged` is declared `true` in the template rather than left implicit.
