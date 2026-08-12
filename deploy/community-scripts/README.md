# Community repository submissions

Files staged for submission to third-party app catalogues. They live here so they
are versioned and reviewable alongside the code they install; the catalogues
themselves need them copied into their own repositories.

## Proxmox — community-scripts

New scripts go to **[ProxmoxVED](https://github.com/community-scripts/ProxmoxVED)**,
their testing repository. PRs adding new scripts directly to ProxmoxVE are closed
without review.

| File here | Goes to |
| --- | --- |
| `ct/fwupd-webui.sh` | `ct/fwupd-webui.sh` |
| `install/fwupd-webui-install.sh` | `install/fwupd-webui-install.sh` |
| `json/fwupd-webui.json` | `json/fwupd-webui.json` |

**The one thing reviewers will question:** this asks for a privileged container
(`var_unprivileged=0`), which their catalogue treats as unusual. The justification is
measurable rather than preferential — an unprivileged container enumerates 2 devices
where a privileged one finds 8, and no combination of capabilities, seccomp or AppArmor
settings closes that gap. Their own `build.func` already emits the required device-cgroup
access for privileged containers via `configure_usb_passthrough()`, so no custom LXC
configuration is needed.

**Not end-to-end tested.** `ct/fwupd-webui.sh` fetches its installer from the
community-scripts repository by name, so it cannot run until it lives there. The install
logic it performs is the same as `deploy/install.sh`, which is tested, and the privileged
container assumption is confirmed on a real Proxmox host. ProxmoxVED exists for exactly
this validation step.

## Unraid — Community Applications

CA does not host templates. It indexes template repositories and scrapes the XML from
them, so `unraid/fwupd-webui.xml` stays where it is and the repository is what gets
registered.

Registration is a request to the CA maintainers rather than a pull request; the entry
point is the Community Applications support thread on the Unraid forums. Once a
repository is listed, CA picks up every template in it automatically and re-scrapes on a
schedule.
