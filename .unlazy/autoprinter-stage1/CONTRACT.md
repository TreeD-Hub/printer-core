# Stage 1 contract (revision 2)

Only durable storage, pairing and queue acceptance. NEVER load or call klippy,
job_queue, file-manager print/start, shell commands or G-code. No inference.
Default feature disabled. All operations remain local/offline in tests.
Browser preparation and later physical executor are out of scope.

## Modules / ownership

- Storage worker: moonraker/components/treed_autoprinter_store.py and
  tools/tests/test_treed_autoprinter_store.py.
- Adapter worker: moonraker/components/treed_autoprinter.py,
  moonraker/components/treed_autoprinter_transport.py,
  tools/tests/test_treed_autoprinter_component.py, moonraker/base/00-core.conf,
  loader/steps/moonraker-config.sh, moonraker/components/README.md,
  moonraker/README.md, tools/tests/README.md, docs/autoprinter.md.
- Backend worker: additive binding_state in treed-back SyncService response,
  schema/OpenAPI/fixture/README and scoped tests only. Other backend stays intact.

## Storage interface (synchronous; adapter invokes via asyncio.to_thread)

AutoPrinterStore(root: Path, printer_id: str, profile_id: str,
 max_asset_bytes=104857600, max_total_bytes=1073741824, max_runs=1000).
Use sqlite3 transactions/full durability; process lock plus thread serialization.
No new dependencies. Standard Python3.11 target, tests available Python3.13.
Storage validation errors: StoreError(ValueError) with safe .code, no secrets.
Adapter and tests import AutoPrinterStore and StoreError from sibling module.

- session() -> {control_epoch,binding_epoch,binding_state,queue_revision}
  defaults 1,1,unpaired,1. Identity persisted; mismatch refuses reuse.
- update_binding(control_epoch,binding_epoch,binding_state,applied_queue_revision)
  validates positive ints, rejects epoch regression. States unpaired/paired/revoked.
  New binding starts a separate namespace at server applied revision; preserve old
  files/queue, never transfer ownership or silently delete. Same binding preserves
  local accepted revision (server can lag after lost ack). No physical readiness.
  Unknown namespace with applied revision above1 must fail closed with
  queue_recovery_required: missing local journal cannot be reconstructed from a
  revision number. No fabricated empty queue. Document explicit recovery needed.
- store_asset(transfer: dict, stream: BinaryIO) -> metadata; current paired binding
  only. Validate UUID, profile, sha256, exact positive size, bounded download, quota.
  Temp file tracked before writing; fsync + atomic replace + directory fsync before
  DB ready marker/receipt. Retry must recover crash after rename, without overwrite
  of differing immutable bytes. No caller path; symlink/path escape denied. Cleanup
  only own incomplete files; never orphan retained data or discard old binding.
- pending_receipts(limit=100) -> list {asset_id,sha256,size_bytes}; current binding.
- confirm_receipt(asset_id) -> durable confirmation, no local file deletion.
- apply_command(command: dict) -> {command_id,state,result}; serialized and durable
  before return. Command IDs immutable; duplicate content returns saved result even
  after expiry/restart; mismatched body conflicts. Validate current control/binding,
  expiry, UUIDs, exact queue revisions, known profile/asset hash and size, unique runs,
  bounded count. enqueue_series appends immutable runs; reorder_queue requires full
  current queued set. Never marks printing/completed. Unknown/physical commands
  rejected with safe code. pause_queue may persist paused; resume and physical
  actions rejected as stage1_execution_disabled. Every queue remains paused.
  Revision mismatch must not invent executed proof; persist uncertain if cannot
  honestly report expected previous revision. Terminal rejection returns previous
  revision, executed returns newly persisted revision. Payload contract from backend.
- pending_results(limit=20) -> durable unacknowledged command result list.
- ack_results(command_ids) -> mark reported, preserve dedup records.
- queue() -> current binding run metadata in order (internal/tests, no file API).
- status() -> safe summary counts/revision/binding/local_pause=true;
  physical_execution_enabled=false; no filesystem paths/secrets/user filenames.
- close() -> release owned resources, only after pending worker operations finish.

Asset namespaces use printer_id+binding_epoch (opaque account binding); no website
user ID needed. Backend never exposes previous binding. Files outside gcode root
and release/generated dirs: printer_data/system/treed-autoprinter by default.
Credentials outside managed code, private JSON file {printer_id,secret}, no logs.
POSIX enforce owner/private file permissions; Windows test equivalents documented.

## Adapter / transport

Existing Moonraker load_component/config/register_endpoint conventions. Default
enabled:false config; no network or storage created while disabled. Status endpoint
and pairing POST use existing authorization, no auth bypass/new public LAN server.
No file read/list/upload API until account-aware local authorization is implemented.
Use bounded stdlib HTTPS via worker thread, verified TLS, fixed configured origin,
no redirects/userinfo/query/fragment, no arbitrary supplied URL. Validate returned
download_path equals api/autoprinter/device/files/<asset UUID>; all URLs generated.
Bound JSON/body sizes, transfer byte counts, timeout, polling/backoff/jitter. One
serialized sync/pairing task; do not block Moonraker event loop. Never log exception
content/headers/body that may include secrets. Do not install a daemon/framework.

Startup generates new agent_boot_id, increasing snapshot_seq per boot. Snapshot:
observed_at UTC Z, bed_epoch0, queue_control paused, current_run_id/run_state null,
local_pause true, ready false, print_paused false, capabilities bed_check false,
model_version null. events and acknowledged_event_ids empty in stage1.

Sync response must validate protocol, printer identity, profile, authority=printer,
positive epochs/revisions, binding_state and bounded commands/transfers before
mutation. Update binding even on control_epoch_mismatch, then resync without
acknowledging previous results. Download/commands only on paired current binding
and valid matching epoch. On successful matching sync without reason, mark the
submitted command result IDs acknowledged; response loss retains them. Reboot keeps
queue/result journal and retransmits same IDs under refreshed control epoch.
401 -> revoked; network errors -> offline (do not delete binding/files); feature
503 unavailable. No fabricated paired status based on epoch alone.
Receipt after verified durable local storage; retry saved receipt if response lost.
Local pairing returns temporary code/expiry only, never device credential. Pairing
status is explicit backend binding_state; offline is separate connection state.

## Backend additive contract

Top-level sync binding_state enum unpaired/paired/revoked. user_id null -> unpaired;
existing response path that suppresses delivery due inactive owner/grant -> revoked;
valid bound active owner -> paired. Credential401 remains separate. No user IDs or
secrets exposed. Update schemas/fixture/README and nearest tests; feature stays off.

## Acceptance

Persist/reopen verified bytes and accepted ordered runs; receipt strictly after
durable bytes; interrupted writes and replay safe; wrong binding/hash/profile/path
rejected; expiry and revision guards; result lost + boot restart no duplicated run;
old binding data retained but inaccessible; every physical command refused; enabled
and disabled component lifecycles tested with fake transport/server and no network.
Run only scoped tests and loader syntax if available, then independent Sol review.
