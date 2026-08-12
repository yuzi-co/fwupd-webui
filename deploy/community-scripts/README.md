# Community repository submissions

Files staged for submission to third-party app catalogues. They live here so they
are versioned and reviewable alongside the code they install; the catalogues
themselves need them copied into their own repositories.

Drafted submission text:

- [`SUBMISSION-proxmox.md`](SUBMISSION-proxmox.md)
- [`SUBMISSION-unraid.md`](SUBMISSION-unraid.md)

Both require a human to send them, and the Proxmox one has a self-review checkbox that
only the submitter can honestly tick.

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

**Written against `AGENTS.md`, not against example scripts.** The first draft copied an
existing script in ProxmoxVE and was wrong in two ways their guidelines catch: the
`build.func` sourcing block has changed (the engine now comes from
`community-scripts/core`), and the JSON needs `repository`, `architectures` and
`platforms`. Their PR template requires disclosing AI assistance and asserts the scripts
were built from `AGENTS.md`, so both files were rewritten against it.

**Not end-to-end tested.** `ct/fwupd-webui.sh` resolves its installer by name from the
community-scripts repositories, so it cannot run until it lives in one. The install logic
it performs is the same as `deploy/install.sh`, which is tested, and the privileged
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
