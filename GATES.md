# Gates: Z-bottom и Eddy acceptance

OWNS: GATES.md, tools/collect_eddy_diagnostic.sh, tools/z_acceptance.py, tools/tests/test_z_acceptance.py, klipper-host/treed_z_recovery.py, klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo.cfg

Scope: OFFLINE PASS подтверждает программный контракт. HARDWARE ACCEPTED требует реальных пакетов и ручного решения; unit tests не закрывают аппаратные gates. Геометрия Z=203 мм измерена на экземпляре, но StallGuard ещё не принят аппаратно.

- [x] G0: Диагностические контракты и численные расчёты проходят офлайн.
  CHECK: python -B tools/tests/test_z_acceptance.py
  EXPECT: Z_ACCEPTANCE_OFFLINE_PASS
  EVIDENCE: локальный запуск 2026-09-26; exit=0; EXPECT=matched. Аппаратный допуск не выполнялся.

- [x] G1: Одна проба Z-bottom, запреты и восстановление состояния проходят офлайн.
  CHECK: python -B tools/tests/test_z_recovery.py
  EXPECT: Z_RECOVERY_CHECKS_PASSED
  EVIDENCE: локальный запуск 2026-09-26; exit=0; EXPECT=matched; upstream Klipper test пропущен без Z_RECOVERY_KLIPPER_SOURCE. Аппаратный допуск не выполнялся.

- [ ] G2: Z-bottom физически повторяем: минимум 10 независимых recovery cycles из нескольких START_Z после явной потери координаты в каждом цикле.
  EVIDENCE: pending; требуется полный CAN/MCU пакет, статистика первых trigger positions в общей координатной опоре и ручное решение по разбросу. Прежние 0.5 мм относились ко второй пробе одного цикла и могут служить только ориентиром до аппаратного обоснования нового допуска.

- [ ] G3: Полный bootstrap завершает bottom_reference, x_home, y_home, eddy_coarse, eddy_probe, final_z0 без retry.
  EVIDENCE: pending

- [ ] G4: Eddy Z0 повторяем: минимум 10 циклов в одной сессии и range не более EDDY_Z0_RANGE_MM (0.05 мм).
  EVIDENCE: pending

- [ ] G5: Сохранены минимум три одинаковые mesh и median/P95/max/RMS разностей.
  EVIDENCE: pending; обязательны suppression до probe_finalize, runtime matrix/params, identity восстановленного save_profile, отсутствие новых diagnostic sections и неизменность существующего pending config. Численный порог mesh пока не установлен, решение принимает оператор.

- [ ] G6: Сопоставлены минимум три независимых cold-start пакета с разными boot_id после ручных перезапусков.
  EVIDENCE: pending

- [ ] G7: Сохранён один полностью идентифицированный стандартный диагностический Eddy scan.
  EVIDENCE: pending; сохранён прежний single-scan gate, runner tools/collect_eddy_diagnostic.sh в режиме eddy-scan.
