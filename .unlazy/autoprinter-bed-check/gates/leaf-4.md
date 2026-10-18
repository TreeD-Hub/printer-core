# Component and executor integration

OWNS: moonraker/components/treed_autoprinter.py, moonraker/components/treed_autoprinter_execution.py, tools/tests/test_treed_autoprinter_component.py, tools/tests/test_treed_autoprinter_execution.py, moonraker/base/00-core.conf, docs/autoprinter.md, docs/autoprinter-bed-check.md, moonraker/components/README.md, tools/tests/README.md

- [x] G1: Candidate recheck is integrated with strict start/completion gates and disabled defaults preserved.
  EVIDENCE: Component/executor keep default-off, live empty-only start/completion
  and current boot/handshake requirements. Local/remote recheck journals candidate
  diagnostics without authorizing motion. Execution module passed all 27 tests
  after fixture repairs; component module passed in the prior combined run.
  Independent reviewer confirmed production fixes and test fixture corrections.
  Existing installer/updater module glob includes new files without infra edits.
