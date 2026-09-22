# Gates: TreeD V2 shutdown recovery and MCU verification

OWNS: GATES.shutdown-recovery.md, runtime-versions.env, loader/**, moonraker/**, runtime-scripts/**, tools/**, docs/**

Scope: Report real MCU firmware separately from host/build state, observe recovery stability without automatic retries, and collect passive CAN diagnostics without printer-side effects.

- [x] G0: this task ledger has valid executable checks
  CHECK: node "C:\Users\Yawllen\.codex\skills\unlazy\scripts\gate-lint.mjs" GATES.shutdown-recovery.md
  EXPECT: LINT OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=ff503967391982ed5601609c4a14572dab012792acea27796cb2319887a4b771; exit=0; EXPECT=matched; output-sha256=d8a742232173bb5a5bc18a959f789e78c8926d0cb3d8544384e3ece1b454f074; output-bytes=286; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Yawllen\Documents\GitHub\printer-core; path=56e1503003b1/38 entries

- [x] G1: mocked firmware, recovery, and passive-diagnostic scenarios satisfy the requested behavior
  CHECK: C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\tests\test_shutdown_recovery_contracts.ps1
  EXPECT: PASS: shutdown recovery contracts
  EVIDENCE: automatic-evidence=v1; definition-sha256=db3fadf8aad5f6fa5eebb8142249d41b470ce826a7f9a6fc2c61f8d442aeae6a; exit=0; EXPECT=matched; output-sha256=8ba3b0c920f7a06da97dca194ceacbcc0739d4e7a95e53c5b55e388f532e6ec4; output-bytes=69; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Yawllen\Documents\GitHub\printer-core; path=56e1503003b1/38 entries

- [x] G2: existing loader and update contracts still pass after the focused changes
  CHECK: C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\tests\test_loader_contracts.ps1 && C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\tests\test_moonraker_update_contracts.ps1 && C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools\tests\test_runtime_update_contracts.ps1
  EXPECT: PASS: reproducible runtime update contracts
  EVIDENCE: automatic-evidence=v1; definition-sha256=f87480500ddbe339798364c795ca112a8afad479be6e1ed0c75af9c5b44a3dcb; exit=0; EXPECT=matched; output-sha256=ad19a7fe0971d447fb72aac20183b2f35b7c6babf9c544fde1770f69545131f9; output-bytes=3622; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Yawllen\Documents\GitHub\printer-core; path=56e1503003b1/38 entries

- [x] G3: implementation keeps flashing, resets, CAN reconfiguration, motion, and heating outside automatic verification and diagnostics
  EVIDENCE: reviewed tools/collect_eddy_diagnostic.sh passive branch and moonraker/components/treed_update.py; passive mode exits before G-code, records firmware_restart_sent=0/can_reconfigured=0, and firmware status has no write path. Explicit recovery sends only one operator-requested FIRMWARE_RESTART.

- [x] G4: final diff preserves the pre-existing loader fixes and contains no unrelated refactor
  EVIDENCE: reviewed diff from base 935268a; exact runtime sync, bundled Mainsail checks, TreeD overlays, dirty guards and CAN txqueuelen=128 remain covered by loader/runtime contract tests. Changes are limited to firmware evidence, passive diagnostics, explicit recovery and their documentation/tests.
