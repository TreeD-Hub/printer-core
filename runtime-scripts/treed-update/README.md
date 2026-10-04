# TreeD Update Service

`treed-update-service` is the root-owned submission and worker entry point installed by `loader/steps/moonraker-config.sh`.

## Contract

- Moonraker submits a request with `requestId`, `targetId`, and `targetTag`; accepted values are `printer-ui` plus `ui-main-<run>-<attempt>`, or `printer-core` plus `vX.Y.Z`.
- `requestId` is a UUID. A repeated ID returns its original operation; another request cannot replace a queued or active operation.
- The worker serializes submissions and execution with `/run/lock/treed-update.lock` and atomically stores the current operation plus the last ten terminal operations in `/var/lib/treed-update/state.json`.
- The state file is root-written and world-readable so Moonraker can poll it. Requests and status messages contain no secrets.
- `treed-update.service` runs one queued operation and invokes recovery if the worker exits before writing a terminal result. `treed-update-recover.service` runs after TreeD Shell at boot. It must not run before Moonraker: the UI itself starts after Moonraker, so that ordering would create a cycle and systemd could skip the API server.
- `treed-update.path` watches the atomic state file and starts the worker when it changes. The submit command also requests a start; either trigger closes the queue-to-systemd interruption window.
- If UI publication was interrupted while its operation-specific rollback directory exists, recovery restores that directory, restarts `treed-shell.service`, checks stable HTTP readiness, and records `rolled_back`. If the rollback directory has already been rotated but the exact target manifest is installed and HTTP-ready, recovery records the update as applied. Otherwise it records an error without claiming rollback.
- UI publication keeps the current bundle in a temporary rollback directory until the new service passes consecutive HTTP readiness checks. If publication or readiness fails, the prior bundle is restored and checked before the result is called `rolled_back`.
- Progress is `0` only while queued, `null` for phases without measured progress, and `100` only on success.
- `printer-core` is a TreeD runtime package, installed by `treed-core-update` under the worker lock. It updates only owned configs, host modules and scripts; it never runs provisioning or updates OS/dependencies/firmware. The legacy shell apply command refuses this target without the managed core worker.
- Core uses GitHub asset SHA-256, a fixed destination allowlist, exact upstream compatibility checks, fsync before-images and a durable journal. The confirmed runtime manifest changes only after stable Klipper/Moonraker readiness; interruptions are recovered through that journal.
- Local overrides, variables, sensor settings, generated configs and the complete `SAVE_CONFIG` block are preserved. Modified managed files and unknown files occupying new destinations reject the update.
- The first loader run with this updater writes a baseline after successful hardware-ready verification. Without the command and baseline, core capability remains unavailable.

## State fields

The API exposes `operationId`, `requestId`, `status`, `phase`, `progress`, `resultCode`, `message`, `targetId`, `targetTag`, `startedAt`, `updatedAt`, and `finishedAt`. Terminal statuses are `applied`, `rolled_back`, `error`, and `rejected`; phases are `queued`, `validating`, `downloading`, `installing`, `restarting`, `verifying`, `rolling_back`, and `complete`.

## Installed files

- `/usr/local/sbin/treed-update-service`
- `/usr/local/sbin/treed-update-apply`
- `/usr/local/sbin/treed-core-update`
- `/etc/systemd/system/treed-update.service`
- `/etc/systemd/system/treed-update-recover.service`
- `/var/lib/treed-update/state.json`
- `/var/log/treed-update/worker.log`
- `/var/lib/treed-update/core-manifest.json` (confirmed installed version/ownership)
- `/var/lib/treed-update/core/<operationId>/` (journal and retained before-images)

The Moonraker sudoers rule allows only the validated `submit` subcommand. No real-device operation is part of the offline tests.

## Release and verification

Build: `python3 runtime-scripts/treed-update/treed-core-update build . OUTPUT/treed-core-runtime.zip`.
The release workflow builds this artifact when `VERSION` changes on `treed-v2`
or a matching `vX.Y.Z` tag is pushed. Runtime stack pins may stay unchanged
when only TreeD components change. See [update architecture](../../docs/update-architecture.md).

Addressed checks: `python -B tools/tests/test_treed_core_update.py`,
`python -B tools/tests/test_treed_update_service.py`,
`python -B tools/tests/test_treed_update_component.py`. Device smoke testing must
cover real stop/start, readiness failure and interrupted installation.
