# ProxmoxVED pull request — draft (ON HOLD)

> **Do not submit yet.** The project fails two of their four Application Requirements
> for new scripts: it must be at least 6 months old (created 2026-08-11) and have 600+
> GitHub stars (has 0). [PR #2172](https://github.com/community-scripts/ProxmoxVED/pull/2172)
> was opened and auto-closed by their validation bot. The draft below stays here for when
> the project qualifies; when that day comes, use their template verbatim from
> `.github/pull_request_template.md` — including the Application Requirements and Source
> sections — and paste this write-up into its Description field.

**Target:** https://github.com/community-scripts/ProxmoxVED (NOT ProxmoxVE — new
scripts opened against ProxmoxVE are closed without review.)

**Before opening this, read the three script files.** Their PR template says scripts
that are clearly AI-generated and not further revised by the author may be closed
without review, and one of the checkboxes asserts you completed a self-review. Only you
can tick that truthfully.

**Files to copy into a fork of ProxmoxVED:**

```
deploy/community-scripts/ct/fwupd-webui.sh              -> ct/fwupd-webui.sh
deploy/community-scripts/install/fwupd-webui-install.sh -> install/fwupd-webui-install.sh
deploy/community-scripts/json/fwupd-webui.json          -> json/fwupd-webui.json
```

---

## Title

```
New script: fwupd-webui
```

## Description

fwupd-webui is a web UI for [fwupd](https://fwupd.org): it lists every device fwupd can
enumerate on the Proxmox host — NVMe drives, SATA disks, HBAs, network cards, Thunderbolt
controllers — with current firmware versions and any updates available from LVFS, and can
flash the devices that are safe to write at runtime.

Read-only by default. Firmware flashing is off unless `FWUPD_WEBUI_ENABLE_FLASHING=true`
is set; while it is off the flash routes are not registered on the application at all.
Even when enabled, every flash requires typing the device name to confirm, storage devices
carry a data-loss warning, and system firmware — `uefi_capsule`, `uefi_dbx` and `mtd` — is
refused outright, because a failed write there costs the motherboard rather than a
peripheral.

**This script asks for a privileged container, which I know is unusual for this
catalogue, so here is the evidence rather than an assertion.** Enumerating firmware means
issuing NVMe admin commands and SCSI generic ioctls against the host's disks. Measured on
a real Proxmox 9.2 host with 8 enumerable devices:

| Configuration | Devices found |
| --- | --- |
| `--privileged` | 8 |
| `--cap-add=ALL` + seccomp and AppArmor unconfined + all mounts | 2 |
| all bind mounts, unprivileged | 2 |
| explicit `--device-cgroup-rule` sets | 0 |

The two that always appear are the CPU and the display, neither of which needs hardware
access. Nothing short of privileged reaches the disks. The script adds no custom LXC
configuration — `build.func` already emits the device access a privileged container needs.

Upstream project: https://github.com/yuzi-co/fwupd-webui (MIT, 198 tests, published
container image, also packaged for Docker and Unraid).

## Prerequisites

- [ ] **Self-review completed** — *left for the submitter; please read the three files first.*
- [x] **Tested thoroughly** — the install logic is in production on a Proxmox 9.2 host
      (privileged Debian 13 LXC, 10 devices enumerated from the host, service healthy).
      See the testing note below for what has and has not been exercised.
- [x] **No breaking changes** — new script only.
- [x] **No security risks** — no hardcoded secrets. The privilege escalation is the
      feature and is justified with measurements above; nothing else is elevated.

## arm64 Support

- [x] **arm64 not supported** — `var_arm64="no"` and `"architectures": ["amd64"]`. Not a
      dependency limitation: fwupd builds fine on arm64, but device enumeration has not
      been verified on arm64 hardware and shipping it untested seemed worse than
      declaring it unsupported.

## AI Assistance

- [x] **AI was used** — I confirm the scripts were built using `AGENTS.md` and
      `.github/agents/pve-script-creator.agent.md` as guidance, and the output has been
      reviewed and corrected to match those guidelines.

Model: **Claude Opus 4.5** (Anthropic), high reasoning effort, driven interactively via
Claude Code. The first draft was written against example scripts in ProxmoxVE and did
**not** match `AGENTS.md` — wrong `build.func` sourcing block, and a JSON file missing
`repository`, `architectures` and `platforms`. Both were rewritten against the templates
in `AGENTS.md` after reading it.

## Type of Change

- [x] ✨ **New feature** — adds a new script.

---

## Testing note — please read

Being precise about what is and is not verified, since the checkbox above is coarse:

**Verified on a real Proxmox 9.2.10 host (kernel 7.0.14-9-pve):**

- A privileged Debian 13 LXC created by the equivalent standalone installer enumerates
  10 devices belonging to the host, including SATA disks and the SPI flash controllers.
- The same install steps this script performs — apt dependencies, venv, systemd unit,
  configuration file — run cleanly and produce a healthy service on port 8099.
- The safety policy behaves correctly against that hardware: BIOS `mtd` devices refused,
  SATA disks gated behind the typed-name confirmation.

**Not verified:** `ct/fwupd-webui.sh` end to end, because it resolves its installer by
name from the community-scripts repositories and therefore cannot run until it lives in
one. That is what I understand ProxmoxVED is for, and I will happily iterate on test
feedback.
