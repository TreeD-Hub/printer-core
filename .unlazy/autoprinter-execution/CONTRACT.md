# Queue executor, revision 1

Current user decisions override the historical server-scheduled plan. Core owns
the durable ordered queue, server mirrors it. No start_run server command exists.
This implementation is checked offline only. Default enabled:false remains;
execution_enabled:false is a separate gate. No SSH/device calls, live tests,
macros/motion/calibration changes, commits, push or deployment.

## Responsibilities

1. Journal/executor worker: existing treed_autoprinter_store.py, new
   treed_autoprinter_execution.py, store tests and new execution tests.
2. Runtime adapter worker: new treed_autoprinter_device.py and its offline tests.
3. Integration worker (after 1/2): treed_autoprinter.py and its tests, base config,
   docs/autoprinter.md, component/test READMEs, moonraker README. No backend logic
   changes expected. Parent approves any necessary contract change first.

## Device interface (async, event loop)

MoonrakerDevice(server) uses verified pinned public component APIs only.
- is_live = True; simulated devices in tests use is_live=False.
- async inspect() -> {ready:bool, state:'idle'|'printing'|'paused'|'complete'|
  'error'|'cancelled'|'unknown', filename:str|null, job_id:str|null,
  queue_empty:bool, queue_safe:bool, service_active:bool,
  phase:str|null}. Unknown data fails closed. No queue reset or next-job API.
- async prepare(run:dict, asset_path:Path) -> relative filename. Name is derived
  solely from binding_epoch/run_id/hash; one distinct filename per run. Copy exact
  verified bytes into gcode root using safe atomic file operations off loop;
  never overwrite different content, follow symlinks, alter G-code or auto-start.
  Inspect END_PRINT AUTO_REMOVE to match immutable completion_profile; ambiguous
  or conflicting directives reject. No changes to physical macros.
- async start(filename) -> None through pinned KlippyAPI.start_print. ACK is
  delivery only; engine must observe matching print_stats before printing state.
- async pause(), async cancel() through pinned APIs. No automatic resume-print.
- async reconcile(run) -> observation with same fields plus evidence_source
  'runtime'|'history'|null. Never infer identity from a recyclable job_id alone.
  Compare unique filename and saved run; unknown outcome must remain blocked.

Adapter must refuse concurrent Moonraker queue (load_on_startup or
automatic_transition true, queued/loading jobs), external active print, service
operations. Does not claim to sandbox privileged console or all UI start paths.
No new dependency; no runtime version change. Model provider absent in production.

## Executor interface (async, called under component's existing _lock)

AutoPrinterExecutor(store, device, process_boot_id, enabled=False, bed_checker=None).
No separate scheduler/queue copy. Store is the only journal and order authority.
Use asyncio.to_thread for DB/files and cancellation-safe completion of mutations.
- async initialize(): durable additive schema migration, boot/recovery state.
- async sync_fields(): agent_boot_id, snapshot_seq, snapshot, events,
  acknowledged_event_ids, command_results. Existing transport adds protocol,
  printer and control epochs. Snapshot contains only observed_at, bed_epoch,
  queue_control, current_run_id, run_state, local_pause, ready, print_paused,
  capabilities:{bed_check}, model_version. It must match durable state; with a
  partial event backlog omit current run identity rather than reporting a future
  run before backend has replayed its events. Event boot identity is in the
  envelope, not an extra field of each event.
- async acknowledge(response, submitted): only exact submitted event IDs and
  receipt IDs, same binding/control/boot and valid response. Ignored events block
  reconciliation, never silently discard/rewrite IDs. Keep all audit records.
  submitted is the exact wire payload plus a local-only binding_epoch. A response
  may repeat IDs in submitted events or submitted acknowledged_event_ids. Persist
  receipts separately until a matching successful response confirms their delivery.
- async command(command): guarded durable command processing/result. Existing
  store apply_command remains compatible/default rejects physical actions.
- async local_action(action): pause_queue/resume_queue/stop_after_current,
  pause_current_print/cancel_current_print/recheck_bed only; current binding required.
  Remote resume cannot clear local_pause. Local resume explicitly clears it,
  but never clears uncertainty/conflict or authorizes a start without fresh proof.
  Local HTTP control accepts {type}; pause/cancel additionally require run_id and
  action_id UUIDs. Match run_id against the actual current run and persist action_id
  for deduplication, so a stale UI request cannot affect the next run.
- async tick(): reconcile current run or choose first queued run, never a second
  while intent/current is unresolved. Does not start if disabled or live provider
  unavailable. Respects pause/revoke/epoch/recovery/outbox capacity and service gate.
- async status(): safe current run/state, queue control, blocked reason and
  execution capability; no paths/secrets/account data of older binding.
- async transport_error(status): HTTP409 persists protocol_recovery_required
  (retired boot or event conflict); HTTP401 pauses. Ordinary network loss does not
  erase an accepted queue or bypass the initial boot handshake.

Component periodic executor task uses same _lock as sync and local actions;
sync/ack must not race execution. Disabled component creates no store/tasks/network.
execution_enabled:false creates no live device calls. Production bed_checker=None
always blocks live start, even if execution_enabled is true. Test fake proof cannot
be supplied by HTTP/config. No public proof ingestion or local file API.

## Durable lifecycle and safety

Additive SQLite migration preserves stage1 files/manifests/results and revisions.
Persist run state, binding, stable filename, start intent, evidence, bed epoch,
consumed check and event outbox before any side effect. Start intent is one-shot;
lost response/restart never retries start. Reconcile exact same run or block.
Do not invent printing on delivery ACK or completed on print_complete/job_loaded.

States follow backend: queued/preparing -> preparing/printing/failed/cancelled;
printing -> cooling/removing/verifying/failed/cancelled; cooling -> removing/
verifying/failed/cancelled; removing -> verifying/failed/cancelled. Completion only
after fresh empty bed proof in verifying. Uncertain reconciles only to printing,
failed/cancelled with real runtime/history evidence; unresolved stays blocked.
Never emit fake cooling/removing stages from a timer. Existing END_PRINT performs
cool/removal; observe its state, do not run a second removal cycle. Stale complete
events from previous runs cannot advance current run. Preserve autonomous mode.

Bed checker test interface: async check(after_run_id, bed_epoch, boot_id) -> full
bed_check payload. Live provider must explicitly be qualified for live use; None
and test providers are never live-qualified. Validate printer, boot, after_run,
bed epoch, UUID/check uniqueness, UTC capture <= now, TTL<=30s, known model,
empty verdict; occupied/unknown/stale/error block. Consume proof atomically with
start intent. Invalidate on restart, service/manual intervention, binding/control
change and each completed print. One check is not reusable across runs.

Queue action command id/hash dedup preserved. Side-effect command intent is saved
before pause/cancel; lost outcome stays uncertain and is never blindly retried.
Remote commands require current control/binding/revision, expiry and run identity.
Reorder only queued runs; keep active/finished records and prevent resurrection.
Revocation hides old namespace, pauses future starts, retains in-flight evidence.
New binding cannot start while an old physical intent is unresolved.

## Wire and crash recovery

Events: event_id UUID, event_seq contiguous starting1 for boot, control_epoch,
type run_state|run_reconciled|bed_check, UTC occurred_at, run_id nullable, payload.
run_state payload={state,error_code?}; reconciliation={state,evidence:{source,
run_id,sha256}}. bed_check={protocol_version:1,printer_id,after_run_id,check_id,
bed_epoch,agent_boot_id,verdict,model_version,captured_at,confidence?,reason?}.
Completed is mirrored by backend bed_check after verifying, NOT run_state completed.
Deliver up to100 events,20 results,100 event-receipts; bound unsent journal and block
new starts before quota exhaustion; never delete unacknowledged data.

Boot handshake: persist transport boot/seq. On process restart pause/invalidate
proof and mark unresolved intent uncertain locally. If prior boot has unsent events
or ack receipts, drain immutable old-boot envelopes BEFORE registering new boot;
new execution prohibited during drain. Once acknowledged, switch to new process
UUID and seq1; accept control_epoch_mismatch then sync current epoch. Retired boot
409 or ignored old-epoch events requires explicit recovery, not ID replacement.
Fresh journal boot handshake completes before execution. On every restart pending
side-effect outcomes are reconciled, never resent as new physical actions.

## Verification

Offline real store/executor with fake device/provider: three accepted run IDs,
exactly three starts, no fourth; dropped start response/restart; IDs reset;
stale/unknown/occupied/error proof; local pause/remote resume; revoked binding;
external print; mismatched file; old completion; immutable outbox lost ack/boot
drain; action dedup and failures. Runtime adapter checked against pinned source,
no real printer. Integration default-off + sync event acknowledgements tested.
Scoped relevant tests only, independent Sol review, final graph detect_changes.
