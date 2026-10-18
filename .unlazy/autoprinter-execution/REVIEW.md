# Independent review and scoped checks

Baseline: dev HEAD 943c4ab3cfbc3f516538646abd7e6f94922db01a. Review includes
unstaged modifications and new untracked modules. No physical execution.

## Device boundary

Reviewer found missing paused phase and unbounded per-run spool amplification.
Worker added paused reconciliation, 1 GiB aggregate spool quota and 16 MiB disk
reserve. Tester: 12 device tests passed, exit 0. Earlier 9-test run was superseded.

## Execution and integration

Accepted review findings being repaired:
- Event acknowledgements passed new IDs in the receipt-confirmation argument.
- Local physical actions omitted run_id and changed their dedup hash on retry.
- Bed proof wire payload lacked required nullable keys and UTC Z normalization.
- Observation cadence depended on sync backoff.
- Physical pause/cancel did not recheck the actual filename.
- Same-epoch revocation exposed current run identity in status.

Parent additionally identified last-started context after preparing and explicit
resume after stop_after_current. These are included in the same bounded fix pass.

Initial store/component run: 30 tests, one failure, one error, two Windows symlink
skips. Both failures were outdated assertions for new session fields and endpoint;
component test cleanup was corrected to close the store even after assertion errors.
Initial execution run: 13 tests, 9 passed, 4 errors, confirming ACK and local dedup
findings. These runs are diagnostics, not acceptance. Final results pending.

Focused source review confirmed the seven execution/integration fixes. Two
compatibility issues remain for a final bounded patch: accept repeated ACK IDs
from submitted receipts, and retain stage1 hashes for remote command deduplication.
Parent verified generic action rejection must omit queue_revision (backend rejects
that field for non-queue commands). Same-epoch revoke needs an explicit regression.

Final: both compatibility findings and generic rejection/local recheck fixes are
closed by independent source review. Final execution run: 21 tests passed, exit 0.
Before that, the combined store/execution/component run passed 48 tests with two
Windows symlink skips; device module passed 12 tests separately. These counts
overlap and must not be added as independent runs. No physical tests were run.
Temporary references and failed-test database were removed after all agents ended.
