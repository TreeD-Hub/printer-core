# Gates: Eddy reproducible diagnostic capture

OWNS: GATES.md, tools/collect_eddy_diagnostic.ps1, tools/tests/test_eddy_diagnostic_contracts.ps1

Scope: Collect one identified Eddy scan with the effective command, aligned host/CAN/Klipper evidence, and no printer-configuration mutation.

- [x] G0: the diagnostic gate ledger has valid, executable checks
  CHECK: node "C:\Users\TreeD\.codex\skills\unlazy\scripts\gate-lint.mjs" GATES.md
  EXPECT: LINT OK
  EVIDENCE: automatic-evidence=v1; definition-sha256=c558a799242f6834d82eb22272c1475a9aae883b3d27c5ace72994b5e0ec6f09; exit=0; EXPECT=matched; output-sha256=085cff8ad1d8b7b1331116dcb09cf3c469e05156879faa8829fe6038c831b43e; output-bytes=150; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\TreeD\Documents\GitHub\treed-mainshellOS; path=81c3dfb5aee4/38 entries

- [x] G1: the diagnostic runner preserves the requested non-mutation boundaries
  CHECK: powershell -NoProfile -ExecutionPolicy Bypass -File tools\tests\test_eddy_diagnostic_contracts.ps1
  EXPECT: PASS: eddy diagnostic contracts
  EVIDENCE: automatic-evidence=v1; definition-sha256=7979788374ad92cb63c8f5259de986679bebfa34266f7eb880093a7e98c9d545; exit=0; EXPECT=matched; output-sha256=a0e3a2295db0b90768d572c420915f26f295f928ba20608671f890d6072c93f2; output-bytes=33; shell=C:\WINDOWS\system32\cmd.exe; cwd=C:\Users\TreeD\Documents\GitHub\treed-mainshellOS; path=81c3dfb5aee4/38 entries

- [ ] G2: one identified standard-area scan has a complete saved evidence package
  EVIDENCE: pending
