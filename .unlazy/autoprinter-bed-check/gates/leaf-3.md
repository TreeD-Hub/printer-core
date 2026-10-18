# E0 provider and runner

OWNS: moonraker/components/treed_autoprinter_bed.py, moonraker/components/treed_autoprinter_bed_inference.py, tools/tests/test_treed_autoprinter_bed.py, tools/requirements-autoprinter-bed-check.txt

- [x] G1: Bounded local candidate inference provider implements the pinned artifact contract and cannot assert empty.
  EVIDENCE: Provider pins E0 size/SHA/version and CPU runtime; bounded capture,
  subprocess cancellation and result validation fail closed. Candidate returns
  occupied/unknown only. Provider module passed 16 tests with 1 Linux-only skip;
  added portable integrity test passed after oracle repair in a fresh process.
  Independent production review findings closed; parent checked repaired oracle.
  Actual weights and ML dependencies were not loaded or installed, per user.
