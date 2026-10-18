# Gates: AutoPrinter core stage1

- [x] N1: Current core extension points and backend protocol verified.
  EVIDENCE: Discovery leaves 1/2 verified; CONTRACT.md revision2; current dev source,
  four indexed repositories, core component/loader coverage checked. Partial config
  coverage verified from source. Backend binding_state addition tested20/181.
- [x] N2: Storage, binding, transport and durable queue acceptance integrated.
  EVIDENCE: SQLite journal, process lock, tracked partial files, receipt retry,
  immutable manifests, dedup, account namespace isolation and explicit revoke
  integrated. Store15tests (2 Windows symlink skips) and component11tests pass.
- [x] N3: No physical commands reachable; disabled mode preserves existing flow.
  EVIDENCE: Default enabled:false; no device API dependencies or file endpoints.
  Disabled/enabled lifecycle and physical-command rejection tested; independent
  reviewer checked actual tracked/untracked code and loader delivery.
- [x] N4: Scoped checks and independent review pass; docs and final diff match scope.
  EVIDENCE: Store15tests/2skips, component11tests, bash-n exit0; backend20/181
  and final fixture Unit8/98. Reviewer confirmed all findings resolved, including
  revocation, read1 timeout, cancellation cleanup and both SQLite retry branches.
  Full graphs refreshed; new3modules+2tests have no recorded parse issues.
  Tests used isolated local files/fake transports; task temp removed. No runtime
  Moonraker, Linux fsync/lock, full suite, device or physical acceptance performed.
