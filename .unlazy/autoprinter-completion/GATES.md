# Приёмка core review fixes

- [x] R1: Локальная stop-after-current сохраняется после remote resume/restart, явный local resume работает, текущая печать не прерывается.
  EVIDENCE: Parent source review; existing local/remote pause and explicit resume cases passed in targeted log. New restart regression passed separately1test exit0 after fixing only submitted fixture epochs, final starts assertion retained. See test-tmp/leaf-10-stop-restart-final.log. Local dev commit3ab5ee0, no physical actions.
- [x] R2: Queued-only reorder проходит при completed/printing/uncertain rows, сохраняет fixed slots и replay; некорректные IDs отклонены.
  EVIDENCE: New mixed-state/replay/invalid IDs test and existing quota/revision/reorder test passed in test-tmp/leaf-10-targeted-unittest.log. Five selected cases each passed: four original PASS plus one affected restart rerun, not a false claim that the initial selection was all green. Parent reviewed exact eligible set and slot-only updates.
