# fwupd Web UI — Phase A Design (Flashing)

**Date:** 2026-08-12
**Status:** Approved
**Scope:** Phase A — flashing runtime-updatable devices. Builds on the phase C read-only inventory,
which is implemented and deployed.
**Supersedes nothing.** The [phase C design](2026-08-11-fwupd-webui-design.md) remains accurate for
everything it covers. Its read-only enforcement had two independent controls; this phase lifts the
second (no `install` argv) exactly as that document anticipated, and leaves the first
(`DisabledPlugins=uefi_capsule`) in place.

## Problem

Phase C answers "what firmware is on this machine, and is any of it out of date". It cannot act on
the answer. Phase A adds the write path: selecting a release and flashing it, with progress visible
while it happens.

Writing firmware is the point at which this tool becomes capable of destroying hardware. The design
below is shaped primarily by that, not by feature completeness.

## What the target hardware actually looks like

Measured on the deployed host rather than assumed. Of eight enumerated devices, five are
`updatable`:

| Plugin | Device | Flags |
| --- | --- | --- |
| `nvme` | CT1000P3PSSD8 | `updatable`, `needs-reboot`, `internal`, `require-ac`, `usable-during-update` |
| `ata` | ST4000VN008-2DR166 (×4) | `updatable`, `needs-reboot`, `internal`, `require-ac`, `usable-during-update` |

Two facts follow, and both drive the design:

1. **Every flashable device on this host reports `needs-reboot`.** Treating `needs-reboot` as a
   blocking condition would disable the feature entirely. It is therefore a warning, never a block.
2. **Four of the five are the array drives.** Rewriting drive firmware under a live array risks the
   array, not merely the drive. They must not be one click away.

## Goals

- Flash a selected release onto a selected device, with live progress.
- Make the dangerous cases require deliberate action, and the safe cases easy.
- Keep the UEFI capsule path structurally unreachable, exactly as in phase C.

## Non-Goals

Held out deliberately, so they do not accrete during implementation:

- **Cancelling a running flash.** See Safety.
- Flash history, audit log, or a firmware-version database.
- Scheduling or unattended updates.
- Batch "update everything".
- Rebooting the host, or activating staged firmware.
- UEFI capsule / BIOS updates. Still `DisabledPlugins=uefi_capsule`.

## What fwupd gives us

Established empirically against fwupd 2.1.7 in the shipped image, not from documentation:

- `fwupdtool install FILE|URI [DEVICE-ID]` — **accepts an https URI directly** and downloads it
  itself, including jcat signature verification. Confirmed by observing `Downloading…: 100.0%`
  before a deliberate 404. We therefore write no download, cache, or signature-checking code.
- `--allow-older` (downgrade), `--allow-reinstall` (reinstall), `--allow-branch-switch`, `--force`.
- A real install of the synthetic test device exits 0 and emits this phase sequence on **stderr**:
  `Loading… → Decompressing… → Writing… → Verifying… → Restarting device… → Waiting… → Idle…`

**Correction to a phase C assumption.** `cli.py` currently documents that fwupdtool's progress bar
appears only on a TTY, so piped output stays clean. That is false for `install`: progress is written
to stderr even through a pipe. It happened to hold for `get-devices`, which is why phase C never
caught it. The comment is wrong and must be fixed as part of this work.

## Safety policy

Four independent controls, outermost first. Each one alone is sufficient to prevent a write; a bug
in any one of them is contained by the others.

### 1. The feature does not exist unless enabled

`FWUPD_WEBUI_ENABLE_FLASHING`, default **false**. When it is not explicitly enabled:

- the flash routes are **not registered on the application at all** — `POST /flash` is a genuine
  404, not a 403 from a handler that decided to refuse;
- no flash controls render anywhere in the UI;
- the device detail view states that flashing is disabled and names the variable, so an operator
  who expected a button learns why there is none.

Not-registered rather than registered-and-refusing is the same reasoning as
`DisabledPlugins=uefi_capsule`: a capability that does not exist cannot be reached by a mistake in
the layer above it.

The practical effect is that **the shipped container behaves exactly as it does today**. An existing
deployment that pulls this release gains no write capability until someone deliberately sets the
variable, and turning it back off is a container restart.

Accepted values are `true`/`false`, `1`/`0`, `yes`/`no`, case-insensitive. Anything else is a
startup error rather than a silent fallback — a typo in this particular variable must not quietly
resolve to "enabled", and a config that means to enable flashing must not quietly resolve to
"disabled" either.

### 2–4. Per-device policy

New module `fwupd/policy.py`. Pure data and predicates — no subprocess, no I/O, no HTTP. It is the
module most likely to be edited later, so it is the one that must be trivial to test.

Three tiers, evaluated per device:

| Tier | Condition | UI |
| --- | --- | --- |
| Allowed | `updatable` **and** plugin on the runtime-safe list | Update button live |
| Blocked, overridable | `updatable` **and** plugin not on the list | Button disabled; unlock by typing the device name exactly |
| Not updatable | no `updatable` flag | No button; reason shown |

The initial allowlist is `nvme` and `thunderbolt`. `ata` is deliberately excluded: on the deployed
host those devices are the array.

The override is checked **server-side**. A disabled button in HTML is a suggestion; the server
refusing the POST is the control.

`needs-reboot` and `require-ac` surface as warnings in the confirm step and in the post-flash
notice. Neither blocks.

`uefi_capsule` remains disabled in the baked `/etc/fwupd/fwupd.conf`, so the ESP staging path stays
unreachable no matter what the allowlist says.

The four controls in summary: the feature is absent unless enabled; the plugin must be allowlisted
or the device name typed exactly; the server enforces both regardless of what the HTML offered; and
the capsule plugin is not loaded at all.

### A guard is being removed

Phase C asserts that the string `"install"` never appears in `cli.py`. Phase A exists to violate
that, so the test is retired **in its own commit**, replaced by:

- policy tests asserting blocked devices are refused, and
- a structural guard asserting `--force` never appears in any argv `cli.py` constructs.

Retiring it silently, folded into a larger change, is how a safety guard disappears unnoticed.

## Architecture

### `fwupd/flash.py` (new)

`FlashJob` holds one operation's state: device, release, operation, phase, percent, status, exit
code, error, and a bounded log tail. `FlashManager` owns at most one job — single-flight, globally.

States: `pending → running → succeeded | failed`.

A finished job is retained so the result remains readable after a reconnect, and is replaced only
when the next flash starts.

### `fwupd/cli.py` (extended)

Gains `install(target, device_id, *, allow_older=False, allow_reinstall=False, on_progress=None)`,
building `fwupdtool --json install <uri-or-path> <device-id>` plus the allow-flags requested.
`--force` is never constructed.

Unlike every existing method, `install` uses `Popen` and reads stderr line by line so progress can
be reported while the process runs. This is the one place the "one blocking `_run`" shape breaks,
and it is confined to this method.

**Progress parsing.** Lines arrive as `Writing…: 42.3%`, interleaved with ANSI-coloured `WARNING:`
lines and timestamped engine chatter (`06:44:19.444 FuEngine failed to coldplug…`). The parser
strips ANSI, matches the `<phase>: <percent>%` shape, and routes everything else to the log tail.

**The percentage is not monotonic.** A captured real run reports `Loading… 7.8%`, drops to `0.0%`,
and later `Restarting device… 68.5%`. The UI therefore shows the current phase name with its
reported percentage beside it and synthesises no global progress bar. A bar that runs backwards
during a firmware write is worse than no bar.

### `fwupd/service.py` (extended)

Grows no job state. It exposes the flash entry point and continues to own the lock; `FlashJob`
state lives in `flash.py` so `service.py` does not become the module that does everything.

### Concurrency

The flash holds the same `asyncio.Lock` that serializes enumeration, so nothing ever runs against
the hardware concurrently.

**Routes must check `manager.active` and redirect before touching the lock.** Awaiting the lock
first would make every page hang for the duration of the flash — the behaviour this design
explicitly rejects.

### Configuration

Two new variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FWUPD_WEBUI_ENABLE_FLASHING` | `false` | Registers the flash routes and UI. Without it this is the phase C container. |
| `FWUPD_WEBUI_INSTALL_TIMEOUT_SECONDS` | `1800` | Hard timeout for a flash. |

The existing `FWUPD_WEBUI_TIMEOUT_SECONDS` (120) continues to govern enumeration. Flashing needs its
own because a slow drive write must not be severed by a timeout tuned for `get-devices`.

The Unraid Community Applications template exposes `FWUPD_WEBUI_ENABLE_FLASHING` as a visible
variable defaulting to `false`, with its description stating plainly that enabling it permits
writing firmware.

## Safety: no cancellation

There is no cancel button, and this is a design decision rather than an omission.

Killing `fwupdtool` between `Writing…` and `Verifying…` is a plausible way to leave a device with
partially written firmware. The job runs to completion or fails on its own.

## UI

**Routes**

| Route | Purpose |
| --- | --- |
| `POST /flash` | Start a job. Form fields: `device_id`, `version`, `operation` (`upgrade`\|`downgrade`\|`reinstall`), and `confirm_name` when overriding. Refused if one is active, or if policy blocks the device and the typed name is absent or wrong. |
| `GET /flash` | Progress page. |
| `GET /flash/progress` | Fragment polled every second via `hx-trigger="every 1s"`. |
| `POST /flash/dismiss` | Clear a finished job, return to inventory. |

Existing routes gain one guard: an active job redirects to `/flash`.

**Confirm step.** Opening Update renders a fragment showing device, current → target version, and
the relevant flags as plain warnings. Allowlisted devices need one confirm click. Blocked devices
render the override input in the same fragment.

Downgrade and reinstall are reached from the device detail's release list and route through the same
confirm fragment with a different operation.

**Post-flash.** Once the job reaches a terminal state and the lock is released, the device is
re-enumerated once. The result view compares the reported version against the target and reads the
device's flags to state either that the firmware is live, or that it is staged and what is required
to finish it. It never offers to reboot or activate.

**Templates.** `_flash_confirm.html`, `flash_progress.html`, `_flash_progress.html`,
`_flash_result.html`. All autoescaped; release changelogs are vendor-supplied strings and never
receive `| safe`.

## Testing

Ordered as the implementation plan will build it:

1. **Policy** — no subprocess at all. Allowlisted permits; non-allowlisted blocks; override accepts
   the exact name and rejects a near-miss; a non-`updatable` device offers nothing.
2. **Progress parsing** — against real captured stderr, committed as a fixture from an actual
   synthetic-device install: 77 lines including ANSI warnings, engine chatter, and the
   non-monotonic percentage.
3. **Job lifecycle** — against a fake CLI. Phase tracking, terminal states, exit code and last phase
   recorded on failure.
4. **Routes** — active job redirects; blocked device refused without the typed name; a second
   `POST /flash` refused while one runs.
5. **Integration** — a real flash of the synthetic device inside the image, asserting exit 0 and the
   expected phase sequence. This is the test that catches fwupd changing its progress format, which
   fixtures alone cannot.
6. **Structural guard** — `--force` never appears in any argv constructed by `cli.py`.

## Risks

**A flash bricks a device.** The residual risk the feature exists to take on. Mitigated by the
enable flag, the allowlist, the typed override, server-side enforcement, and no cancellation. Not
eliminated.

**The enable flag becomes ambient.** Someone sets `FWUPD_WEBUI_ENABLE_FLASHING=true` to perform one
update and leaves it on, at which point the outermost control is gone permanently. Mitigated only by
documentation, which is a weak mitigation and is recorded here as a known limitation rather than a
solved problem. A future phase could expire the capability after a period, but timed capabilities
that lapse mid-operation are their own hazard and are not worth building for a single-operator NAS.

**Array drives flashed under a live array.** The most damaging realistic outcome on this host.
Mitigated by keeping `ata` off the allowlist so it requires typing the device name.

**fwupd changes its progress output.** The parser is best-effort by construction: unrecognised lines
go to the log tail rather than failing the job, so a format change degrades progress display without
breaking the flash. The integration test is what surfaces it.

**Staged-but-not-live firmware misread as applied.** Every flashable device on this host reports
`needs-reboot`, so this is the normal case, not the exception. The result view states it explicitly.
