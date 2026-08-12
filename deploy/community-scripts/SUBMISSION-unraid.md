# Unraid Community Applications — submission draft

CA does not host templates. It indexes template *repositories* and scrapes the XML from
them, so the repository itself is what gets registered.

**The repository layout now follows
[unraid/unraid-community-apps-starter](https://github.com/unraid/unraid-community-apps-starter),
the official starter template:**

```
ca_profile.xml               repository profile shown in CA  (must be at the root)
icon.svg                     repository icon, referenced by ca_profile.xml
icon.png                     application icon, referenced by the template
templates/fwupd-webui.xml    one XML per Docker app
LICENSE                      OSI-approved licence is required before submitting
```

**Submission is now a self-service flow, not only a forum post.** The starter README ends
with: *"Run Validate and Scan in the Community Apps submit flow: `/submit`."* Use that
first — the forum route below is the older path and the fallback if the flow rejects
something you cannot explain.

## Where to go

Verified by following CA's own links rather than from memory:

| What | Where |
| --- | --- |
| **Submission instructions** | https://forums.unraid.net/topic/57181-docker-faq/#comment-566084 |
| CA support thread | https://forums.unraid.net/topic/38582-plug-in-community-applications |
| Application policies (locked) | https://forums.unraid.net/topic/87144-ca-application-policies-notes/ |

The first link is the authoritative one: the policies thread ends with *"To get your apps
added to CA, see HERE"* and points there. Read it before posting — its body is rendered
client-side and could not be extracted here, so treat the procedure below as the shape of
the request rather than the exact steps.

## Policy requirements that apply to this app

From the policies thread, the ones worth checking before submitting:

- **Open source** — required for author-created applications. MIT. ✅
- **2FA must be enabled** on the GitHub repository, with an acknowledgement given to the
  CA authors/maintainers or Limetech. **Verify this on the `yuzi-co` account before
  posting** — it is a stated requirement, not a nicety.
- **No referral or affiliate links** in Project/Support URLs. ✅
- **A reasonable description** is a minimum standard; templates failing it are removed
  automatically. ✅
- **"Proof of concept" applications are generally not accepted**, and if accepted must
  say so in the description. This project is new, so a moderator may reasonably raise it.
- **Data-loss bugs attract severe moderation, up to blacklisting the whole repository**,
  and moderators explicitly err on the side of users over authors. That is the single
  most relevant policy here: this application can write firmware. The read-only default
  and the confirmation model are worth stating prominently, which the draft below does.
- **Duplicate of an existing container** is refused. No equivalent exists in CA. ✅

One practical note: CA sources download statistics from Docker Hub. This image is
GHCR-only, so the listing will show no download counts. Not a blocker, just expected.

## The post

Registration is a request to the CA maintainers on the forums, not a pull request.

Post this from your own forum account:

---

**Subject / opening line:** Request to add a template repository — fwupd-webui

Hi, could I have the following repository added to Community Applications?

**Repository:** https://github.com/yuzi-co/fwupd-webui
**Template:** `templates/fwupd-webui.xml`
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
- [ ] `icon.png` resolves over raw.githubusercontent.
- [ ] The template's `Overview`, `Support`, `Project`, `Icon`, `Category` and `WebUI`
      fields are populated and accurate.
- [ ] `Privileged` is declared `true` in the template rather than left implicit.
