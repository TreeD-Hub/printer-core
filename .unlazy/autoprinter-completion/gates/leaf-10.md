# Исправления core

OWNS: moonraker/components/treed_autoprinter_execution.py, moonraker/components/treed_autoprinter_store.py, tools/tests/test_treed_autoprinter_execution.py, tools/tests/test_treed_autoprinter_store.py, docs/autoprinter.md, docs/autoprinter-todo.md

- [x] G1: P1 local stop priority и P2 queued-only reorder исправлены и проверены ближайшими fake-device/Store regression tests; no physical action.
  EVIDENCE: Parent accepted six-file source/test/docs diff, verified targeted4passed + affected restart1passed; no production gate weakened. Graph4changed Python paths metadata_match, bounded impact56symbols. Exact six-file dev commit3ab5ee09b7a6eeb6f54ae1fb3e2af29560359c1d; history bound retained and TODO documented.
