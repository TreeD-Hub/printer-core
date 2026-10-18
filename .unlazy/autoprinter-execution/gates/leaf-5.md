# Component integration

OWNS: moonraker/components/treed_autoprinter.py, tools/tests/test_treed_autoprinter_component.py, moonraker/base/00-core.conf, docs/autoprinter.md, moonraker/components/README.md, tools/tests/README.md, moonraker/README.md

- [x] G1: Executor integration preserves disabled defaults, serialized sync/actions and safe shutdown; offline tests and documentation match wire contract.
  EVIDENCE: Component tests passed in 48-test combined run (2 store symlink skips).
  Separate execution cadence, shared lock and shutdown independently reviewed.
  Default enabled:false and execution_enabled:false preserved; live provider absent.
