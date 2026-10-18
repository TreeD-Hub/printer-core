# Pinned runtime adapter

OWNS: moonraker/components/treed_autoprinter_device.py, tools/tests/test_treed_autoprinter_device.py

- [x] G1: Adapter validates files/runtime and implements pinned APIs without device execution.
  EVIDENCE: Pinned source verification, independent review, paused phase and spool
  quota fixes; 12 offline device tests passed with exit 0. No hardware calls.
