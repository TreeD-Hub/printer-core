# TreeD Update Service

`treed-update-service` is the root-owned submission and worker entry point installed by `loader/steps/moonraker-config.sh`.

## Source files

- `treed-update-service` — validated submission, queue, execution and recovery.
- `treed-update-apply` — UI bundle publication, readiness checks and rollback.
- `treed-core-update` — TreeD runtime package build, inspection, installation and recovery.
- `treed-loading-screen` — best-effort splash start/stop around a managed update; it does not change persistent display mode.
- `treed-update.service`, `treed-update.path`, `treed-update-recover.service` — systemd worker, queue activation and boot recovery.

## Contract

- Moonraker submits a request with `requestId`, `targetId`, and `targetTag`; accepted values are `printer-ui` plus `ui-main-<run>-<attempt>`, or `printer-core` plus `vX.Y.Z`.
- A core request may include `uiTargetTag` (the fourth submit argument). The worker persists this UI step before starting, runs core then UI under the same lock and operation ID, and stops on core failure. Recovery preserves a queued second step. `supportsCombinedUpdate` advertises this API; older services must not receive combined requests.
- `requestId` is a UUID. A repeated ID returns its original operation; another request cannot replace a queued or active operation.
- The worker serializes submissions and execution with `/run/lock/treed-update.lock` and atomically stores the current operation plus the last ten terminal operations in `/var/lib/treed-update/state.json`.
- The state file is root-written and world-readable so Moonraker can poll it. Requests and status messages contain no secrets.
- `treed-update.service` runs one queued operation and invokes recovery if the worker exits before writing a terminal result. `treed-update-recover.service` runs after TreeD Shell at boot. It must not run before Moonraker: the UI itself starts after Moonraker, so that ordering would create a cycle and systemd could skip the API server.
- `treed-update.path` watches the atomic state file and starts the worker when it changes. The submit command also requests a start; either trigger closes the queue-to-systemd interruption window.
- If UI publication was interrupted while its operation-specific rollback directory exists, recovery copies it back, restarts `treed-shell.service`, checks stable HTTP readiness, and records `rolled_back`. The copy remains available if recovery itself is interrupted. A failed apply process also enters this path before recording its result. If the rollback directory has already been rotated but the exact target manifest is installed and HTTP-ready, recovery records the update as applied. Otherwise it records an error without claiming rollback.
- UI publication keeps the current bundle in a temporary rollback directory until the new service passes consecutive HTTP readiness checks. If publication or readiness fails, the prior bundle is restored and checked before the result is called `rolled_back`.
- Progress is `0` only while queued, `null` for phases without measured progress, and `100` only on success.
- `printer-core` is installed by `treed-core-update` under the worker lock. Releases with a provisioning revision also carry `treed-mainshellos-source.zip`; when the revision, pinned upstream refs, or required units differ, the worker validates that exact-tag source against the runtime manifest, installs runtime files, then runs the managed loader with settings preserved and the existing UI retained. Legacy manifests without a provisioning revision remain runtime-only. It never updates the OS or firmware.
- Core uses GitHub asset SHA-256, a fixed destination allowlist, exact upstream compatibility checks, fsync before-images and a durable journal. The confirmed runtime manifest changes only after stable Klipper/Moonraker readiness; interruptions are recovered through that journal.
- Before core heating shutdown, the worker acquires the Klipper `_TREED_UPDATE_GATE`; its single G-code command validates idle, pause, virtual-SD, manual-probe, Z-recovery and calibration state, then blocks supported `START_PRINT`/`RESUME` guards plus `SDCARD_PRINT_FILE` and `M24`. The G-code queue serializes admission with active G-code operations. After posting the command, the worker queries the macro state and requires `active=1` for BEGIN or `active=0` for END. Unknown commands, missing status and mismatched state prevent heater or service effects when BEGIN is not confirmed; a failed END remains a visible cleanup error. A gate left active after the worker has stopped is cleared by restarting Klipper; restart only after confirming the update worker is no longer running.
- Recovery reacquires the same gate before service stop or file restoration. If Klipper cannot serve the gate, recovery proceeds only when systemd confirms Klipper is inactive or failed. It then stops both services and verifies their stopped state. An active Moonraker does not require manual intervention; unknown or active Klipper without a confirmed gate still blocks mutation.
- A normal core update runs the already installed updater. It installs the target updater and admission macros in the same runtime transaction before restarting Klipper; the next update uses the new admission protocol. Existing installations do not need a manual loader run just to introduce this gate. Copying only the new updater onto an old running configuration is unsupported and fails closed. The first transition still uses the old updater's safety checks; a new package cannot retroactively change that running code.
- The source archive is extracted through bounded path checks into the operation journal directory. Source version, pinned upstream refs, provisioning revision, and runtime mapped files must match the verified target package. Downloads and extracted files are flushed before runtime mutation. Recovery rechecks the retained source against the archive, validates installed files, and repeats the managed loader for the same operation. It never runtime-rolls back after provisioning begins. Readiness and upstream compatibility must pass before committing the manifest.
- A new request after interrupted provisioning resumes the saved operation, target release, reset consent and pending UI step. Moonraker can submit this recovery while Klipper is unavailable; the root worker still enforces gate/systemd safety. A validation error before journal creation permits an ordinary fresh attempt. The loader log is appended on retry. Worker/recovery time budgets cover the 25-minute loader limit; the outer systemd timeout kills the whole cgroup, rather than only the parent updater.
- The managed loader leaves runtime files prepared by the invoking updater unchanged. Old updaters can therefore install the new package without invalidating their recorded hashes. Inert legacy AutoPrinter flags can remain after this first transition; the new updater removes them during preparation of the next package. Full loader configuration merge also removes them. New rendering accepts both `:` and `=` separators while preserving device identity and credential paths. Managed provisioning does not reinstall updater commands already delivered by the core transaction; standalone command installation and updater unit replacement use a flushed temporary file and rename.
- An AutoPrinter payload must contain all seven `treed_autoprinter*.py` modules. Readiness, recovery and rollback require `treed_autoprinter` when the relevant manifest includes it, and reject required names in `failed_components`. A legacy runtime without AutoPrinter can still be upgraded. Credentials, account files, the local store and external model weights stay outside the payload; ML dependencies are not installed automatically.
- Local overrides, variables, sensor settings, generated configs and the complete `SAVE_CONFIG` block are preserved. Modified managed files without explicit per-file reset consent and unknown files occupying new destinations reject the update.
- `treed-core-update inspect` reads the installed baseline and lists all modified managed files, including camera clients, host modules and updater commands, with their current SHA-256. The check API retains `supportsConfigReset` and `configConflicts`; no files are changed by inspection. The UI offers explicit per-file reset during the core update; printer configuration settings can be moved into `local_overrides.cfg` before another check.
- `resetConfigs` contains `{path, sha256}` selections. Submission keeps the optional UI tag as the fourth argument (empty for core only) and passes this list as JSON in the fifth argument. The root queue persists it for recovery. Only allowed files owned by the installed baseline can be reset; a changed hash rejects stale consent. The verified new package supplies the contents or removes a file no longer included, device `SAVE_CONFIG` remains preserved, and the existing transaction journal retains the old files for backup and rollback. An interruption before runtime writes restarts services without restoring stale before-images over fresh local edits.
- The first loader run with this updater writes a baseline after successful hardware-ready verification. Without the command and baseline, core capability remains unavailable.
- After a successful standalone loader run, `treed-update-service complete-loader` closes an earlier failed core transition under the worker lock. It accepts only the same or a newer installed release after `treed-core-update verify` checks managed file hashes, pinned upstream compatibility and stable readiness. Active operations and failed verification remain unchanged; a pending UI step stays queued. This lets a repository-delivered loader repair an interrupted older release without editing its cached archive or manually changing update status.

## State fields

The API exposes `operationId`, `requestId`, `status`, `phase`, `progress`, `resultCode`, `message`, `targetId`, `targetTag`, `startedAt`, `updatedAt`, and `finishedAt`. Terminal statuses are `applied`, `rolled_back`, `error`, and `rejected`; phases are `queued`, `validating`, `downloading`, `installing`, `restarting`, `verifying`, `rolling_back`, and `complete`.

## Installed files

- `/usr/local/sbin/treed-update-service`
- `/usr/local/sbin/treed-update-apply`
- `/usr/local/sbin/treed-core-update`
- `/usr/local/sbin/treed-loading-screen`
- `/etc/systemd/system/treed-update.service`
- `/etc/systemd/system/treed-update.path`
- `/etc/systemd/system/treed-update-recover.service`
- `/var/lib/treed-update/state.json`
- `/var/log/treed-update/worker.log`
- `/var/lib/treed-update/core-manifest.json` (confirmed installed version/ownership)
- `/var/lib/treed-update/core/<operationId>/` (journal and retained before-images)

The Moonraker sudoers rule allows only the validated `submit` subcommand. No real-device operation is part of the offline tests.

## Release and verification

Build the runtime artifact with `python3 runtime-scripts/treed-update/treed-core-update build . OUTPUT/treed-core-runtime.zip`.
The release workflow also packages the matching source archive from the same tag. It builds these artifacts on pushes to `treed-v2` or manual runs,
using version `0.<run_number>.<run_attempt>` and tag `v0.<run_number>.<run_attempt>`,
as in the UI workflow. It stamps the job checkout and source asset `VERSION`
without committing to the branch; runtime and release manifests share that version.
Manual `vX.Y.Z` tags must still match the committed `VERSION`.
PRs do not need a version bump. Runtime stack pins may stay unchanged
when only TreeD components change. See [update architecture](../../docs/update-architecture.md).

Addressed checks: `python -B tools/tests/test_treed_core_update.py`,
`python -B tools/tests/test_treed_update_service.py`,
`python -B tools/tests/test_treed_update_component.py`, `python -B tools/tests/test_managed_loader.py`,
and `python -B tools/tests/test_klipper_preserve.py`. Upgrade fixtures execute the historical updaters
from `0f7d771` and `4c3e11d`, then perform the next update with the new updater. Filesystem transitions
are real; network, systemd and hardware readiness are isolated. Device smoke testing must
cover real stop/start, readiness failure and interrupted installation.

Related documentation: [Moonraker API](../../moonraker/components/README.md#назначение-treed_updatepy),
[UI delivery](../../docs/ui-runtime-delivery/README.md),
[offline checks](../../tools/tests/README.md).
