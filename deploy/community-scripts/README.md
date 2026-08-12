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

> **BLOCKED on eligibility, not on the code.** Do not submit yet.
>
> Their PR template carries an Application Requirements section that new scripts must
> satisfy, enforced by a bot that closes non-compliant PRs automatically:
>
> | Requirement | Status |
> | --- | --- |
> | At least 6 months old | created 2026-08-11 ❌ |
> | 600+ GitHub stars | 0 ❌ |
> | Actively maintained | ✅ |
> | Official release tarballs | ✅ (v0.1.0, v0.1.1) |
>
> A first attempt — [ProxmoxVED#2172](https://github.com/community-scripts/ProxmoxVED/pull/2172)
> — was opened and auto-closed for missing that template section. Reopening was not
> attempted: their bot asks explicitly that maintainers not be pinged about closed PRs,
> and fixing the template would not fix the two failing thresholds. Ticking those boxes
> to get through would be false.
>
> The files below are ready and correct; the project is not old or known enough yet.
> Revisit around **February 2027** at the earliest, or once the repository has traction.
> Their bot notes the team periodically revisits closed submissions of projects that
> prove valuable, so #2172 stands as a marker.

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

### Lesson worth keeping

The script files were checked carefully against `AGENTS.md` and the repo's conventions,
and the *eligibility rules* were not checked at all — they sit further down the same PR
template that was only read in part. That is the wrong order: no amount of correct
formatting matters if the project cannot be submitted yet. Check whether a catalogue will
accept a project before writing anything for it.

## Unraid — Community Applications

CA does not host templates. It indexes template repositories and scrapes the XML from
them, so `templates/fwupd-webui.xml` stays where it is and the repository is what gets
registered.

Registration is a request to the CA maintainers rather than a pull request; the entry
point is the Community Applications support thread on the Unraid forums. Once a
repository is listed, CA picks up every template in it automatically and re-scrapes on a
schedule.

**Still viable.** CA publishes no age or popularity thresholds, unlike community-scripts,
so this submission is not blocked — see [`SUBMISSION-unraid.md`](SUBMISSION-unraid.md).
