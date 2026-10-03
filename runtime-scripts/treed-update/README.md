# TreeD Update Service

`treed-update-service` is the root-owned submission and worker entry point installed by `loader/steps/moonraker-config.sh`.

## Contract

- Moonraker submits a request with `requestId`, `targetId`, and `targetTag`; accepted values are `printer-ui` plus `ui-main-<run>-<attempt>`, or `printer-core` plus `vX.Y.Z`.
- `requestId` is a UUID. A repeated ID returns its original operation; another request cannot replace a queued or active operation.
- The worker serializes submissions and execution with `/run/lock/treed-update.lock` and atomically stores the current operation plus the last ten terminal operations in `/var/lib/treed-update/state.json`.
- The state file is root-written and world-readable so Moonraker can poll it. Requests and status messages contain no secrets.
- `treed-update.service` runs one queued operation and invokes recovery if the worker exits before writing a terminal result. `treed-update-recover.service` runs before Moonraker at boot.
- `treed-update.path` watches the atomic state file and starts the worker when it changes. The submit command also requests a start; either trigger closes the queue-to-systemd interruption window.
- If UI publication was interrupted while its operation-specific rollback directory exists, recovery restores that directory, restarts `treed-shell.service`, checks stable HTTP readiness, and records `rolled_back`. If the rollback directory has already been rotated but the exact target manifest is installed and HTTP-ready, recovery records the update as applied. Otherwise it records an error without claiming rollback.
- UI publication keeps the current bundle in a temporary rollback directory until the new service passes consecutive HTTP readiness checks. If publication or readiness fails, the prior bundle is restored and checked before the result is called `rolled_back`.
- Progress is `0` only while queued, `null` for phases without measured progress, and `100` only on success.
- `treed-update-apply printer-core ...` no longer checks out a Git tag or runs the regular loader. The worker rejects this target until a signed system bundle, verified A/B boot backend, and post-boot health confirmation are implemented.

## State fields

The API exposes `operationId`, `requestId`, `status`, `phase`, `progress`, `resultCode`, `message`, `targetId`, `targetTag`, `startedAt`, `updatedAt`, and `finishedAt`. Terminal statuses are `applied`, `rolled_back`, `error`, and `rejected`; phases are `queued`, `validating`, `downloading`, `installing`, `restarting`, `verifying`, `rolling_back`, and `complete`.

## Installed files

- `/usr/local/sbin/treed-update-service`
- `/usr/local/sbin/treed-update-apply`
- `/etc/systemd/system/treed-update.service`
- `/etc/systemd/system/treed-update-recover.service`
- `/var/lib/treed-update/state.json`
- `/var/log/treed-update/worker.log`

The Moonraker sudoers rule allows only the validated `submit` subcommand. No real-device operation is part of the offline tests.
