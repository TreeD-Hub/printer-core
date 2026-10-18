# Bed-check contract, revision 1

Scope: printer-core dev, baseline 8f1944f99906122de4045a909abd85620ea69511.
User explicitly deferred dependency installation and real-weight inference.
Only scoped offline tests with fake inference/camera are allowed. No physical
commands, SSH, Git write actions, deploy, training or source release changes.

## Artifact and qualification

Use RF-DETR Small E0 from printer-defect-server/models/releases/
fdm-preprocessing-20261016/detection/E0/training_5epochs/checkpoint_best_total.pth.
Size 127479855, SHA256
8de7e8aa22e20893a563c0c04d28f0019dcad9ef8d805818df6fd2ec8b10866e.
Version: fdm-detail-e0-20261016-8de7e8aa22e2. Resolution512, RGB full original
frame, one foreground class0 named деталь. Threshold0.5191324353218079, NMS0.8.
Do not substitute classifier, pretrained COCO weights, E2, random weights, image
enhancement or a threshold chosen on test. Weights remain external, not copied
into this repository. Verify identity before deserialization; reject symlinks and
nonregular files. No unsafe torch.load fallback, trust_checkpoint stays false.

Pinned inference dependencies from existing server: rfdetr1.11.0, torch2.11.0,
torchvision0.26.0, Pillow12.3.0. Exact upstream tag1.11.0 supports
RFDETRSmall(pretrain_weights=path,num_classes=1,resolution=512,device='cpu').
No downloads permitted inside runner; use offline env and fail-closed socket guard.
Do not install/run these dependencies in this task.

E0 test had 806TP/12FN out of818 boxes, all test frames contained parts. There
are no empty-bed/background negatives. Therefore E0 is a candidate occupancy
detector, not a qualified empty-bed safety provider. live_qualified=False is
hardcoded and cannot be enabled through config/HTTP. Valid foreground =>occupied;
no foreground =>unknown with reason model_not_qualified; errors =>unknown.
Never return empty from this E0 provider. Existing qualified test providers remain
usable in offline executor tests; no new bypass for real starts.

## Provider and subprocess boundary

NEW treed_autoprinter_bed.py defines shared identity/constants and
E0BedChecker(printer_id, model_path, python_path, snapshot_url).
- Constructor only validates config; no camera, process, file hash or heavy imports.
- model_version fixed as above; allowed_model_versions contains only that version;
  live_qualified read-only false; status() returns safe diagnostic summary, no paths.
- async check(after_run_id,bed_epoch,boot_id) -> complete bed_check payload:
  protocol_version1,printer_id,after_run_id,check_id UUID,bed_epoch,agent_boot_id,
  verdict occupied|unknown,model_version,captured_at UTC Z,confidence nullable,
  reason nullable/short stable code. Each check obtains new UUID and fresh capture.
- Capture uses separately configured local loopback snapshot URL, no active print
  session file. Bounded request/read, maximum8MiB, JPEG marker/type validation,
  cache-busting request, no proxies, credentials or redirects. Reject HTTP Age>0,
  invalid/cached/error frames; request receipt time is not proof of sensor freshness.
  This camera freshness limitation is another reason empty remains unavailable.
- async serialized checks, off-loop capture, cancellation waits for bounded thread;
  no persisted camera frames. Hard overall deadline<=25sec, captured proof<=30sec.
- Child process: configured absolute interpreter + adjacent inference script,
  shell=False, JPEG bytes stdin, stderr discarded, bounded stdout JSON. Timeout or
  cancellation kills and reaps child; no background leftovers. No frame temp files.
- status retains only last diagnostic result/time/reason, no frames, credentials,
  raw child output, account data or model path. Component hides it when not paired.
  On capture failure unknown uses check-start UTC time in captured_at; its reason
  explicitly says no usable image was acquired. This is not sensor-freshness proof.

NEW treed_autoprinter_bed_inference.py is a one-shot read-only runner. Imports
torch/RF-DETR/Pillow only inside inference path, enforces dependency versions,
artifact size/hash, CPU, class names, full JPEG dimensions/decoding limits and
finite scores/bounded original-frame xyxy/class0. Uses pinned prediction API and
NMS. Stdout contains ONLY one bounded result JSON (redirect library noise); errors
produce safe unknown/error code, not exception text. Socket guard blocks network
during model import/load/predict, including attempted fallback downloads.
Runner targets Linux/Radxa and passes a verified open descriptor through
/proc/self/fd; lack of procfs fails closed. Windows real inference is unsupported
by this runner, independently of the user-deferred dependency installation.

Runner result exact shape: schema_version1,model_version,model_sha256,
frame_sha256,width,height,detail_count,confidence (nullable); errors use
schema_version1,error (short fixed code). Provider verifies result identity,
frame digest, integer dimensions/count, finite confidence and no extra fields.
No raw proofs or arbitrary executable command is accepted from an HTTP endpoint.

## Integration

Component constructs checker when bed_check_enabled=true (defaultfalse), even
execution_enabled=false so explicit recheck can observe without enabling printing.
Config: bed_check_enabled,bed_check_model_path,bed_check_python,
bed_check_snapshot_url (default existing127.0.0.1:8080 snapshot endpoint).
Unconfigured/invalid provider fails closed and reports safe diagnostic reason;
disabled component creates no resources. No heavy inference inside Moonraker.

Executor _bed_proof remains strict for start/completion. Extend with an internal
require_empty keyword defaulttrue: strict path preserves live-qualified guard and
empty-only proof. Explicit recheck uses require_empty=false, permits occupied or
unknown evidence from candidate; unqualified empty is rejected. Validate all
context/TTL/model/wire fields for both. Only qualified empty may call
execution_complete; occupied/unknown never complete or start a run. No backend
protocol/schema changes. Generic recheck command results remain durable/deduped.
Diagnostic recheck requires initialized executor, no pending old-boot drain,
active transport boot equal to process boot, and a successful handshake for that
process. This prevents proofs for a new boot/control generation entering old events.
Local status includes safe bed_check diagnostic only when paired. Snapshot stays
the existing exact wire shape; capabilities.bed_check=false for this candidate.
Backend allowed_model_versions remains deployment configuration, defaultempty;
document required version without modifying backend/runtime state.

Delivery: existing installer/updater treed_*.py glob must be verified to include
both new modules. No default dependency install, model copying, service startup,
camera macro, motion, removal, calibration or legacy spaghetti reaction changes.

## Ownership and verification

Leaf3 owns new provider, new runner, new provider tests and optional requirements
file tools/requirements-autoprinter-bed-check.txt.
Leaf4 owns component/executor integration and their tests, baseconfig,
docs/autoprinter.md,new docs/autoprinter-bed-check.md,components README,
tools/tests README. Parent owns all ledgers. No overlapping writes.

Offline tests: fake current JPEG/runtime =>occupied; no boxes=>unknown; invalid
class/score/geometry/result identity, camera cache/failure/size/redirect, altered
weights and unavailable runtime failclosed; timeout/cancel child reaped; fresh
check IDs and correct context; repeated/stale proof rejected; candidate empty
cannot reach real start; recheck occupied/unknown cannot complete verifying run;
default-off creates no capture/inference; delivery glob and wire reviewed.
Tester runs only relevant unittest modules; reviewer independently reads actual
diff/new files against baseline. Real weights not loaded by explicit user choice.
