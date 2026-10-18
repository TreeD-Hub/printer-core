# Local API for AutoPrinter UI

OWNS: moonraker/components/treed_autoprinter.py, moonraker/components/treed_autoprinter_store.py, moonraker/components/treed_autoprinter_execution.py, tools/tests/test_treed_autoprinter_component.py, tools/tests/test_treed_autoprinter_store.py, tools/tests/test_treed_autoprinter_execution.py, docs/autoprinter.md

Contract: G:/GitHub/printer-ui/.unlazy/autoprinter-ui/CONTRACT.md revision2.
Core dev baseline88d415c9517e0c42f6ffdf16d047288f863db9e2. No physical action,
SSH, install, Git write/deploy or actual inference. Only local source and fake tests.

- [x] G1: Local queue/status and action result additions preserve privacy, durable dedup and backend wire.
  EVIDENCE: Python -B -W error::RuntimeWarning -W error::DeprecationWarning -m
  unittest tools.tests.test_treed_autoprinter_store
  tools.tests.test_treed_autoprinter_execution tools.tests.test_treed_autoprinter_component
  -v: 66 tests OK, skipped=2 (Windows symlink), exit 0, 238.128s. Separate bounded
  projection regression 1/1 passed in 3.122s. Windows sandbox async IPC stalled;
  authorised local-IPC repeat completed. Independent Sol review confirmed local
  receipt/dedup and namespace privacy; backend wire unchanged. Graph refreshed
  2026-10-18T13:14:00Z, all 6 relevant source/test paths no recorded coverage issue;
  detect_changes base dev/88d415c9517e0c42f6ffdf16d047288f863db9e2 inspected.
  Task test-tmp removed after agents finished, verified absent. No hardware,
  actual inference, install, build, commit/push/PR/deploy.
