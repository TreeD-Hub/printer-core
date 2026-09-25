# Gates: Z-bottom и Eddy acceptance

OWNS: GATES.md, tools/collect_eddy_diagnostic.sh, tools/z_acceptance.py, tools/tests/test_z_acceptance.py, klipper-host/treed_z_recovery.py, klipper/profiles/treed_v2_corexy_v1/probe_eddy_duo.cfg

Scope: OFFLINE PASS подтверждает программный контракт. HARDWARE ACCEPTED требует реальных пакетов и ручного решения; unit tests не закрывают аппаратные gates. Номинальные 255 мм не являются измерением механики.

- [x] G0: Диагностические контракты и численные расчёты проходят офлайн.
  CHECK: python -B tools/tests/test_z_acceptance.py
  EXPECT: Z_ACCEPTANCE_OFFLINE_PASS
  EVIDENCE: automatic-evidence=v1; definition-sha256=aab4b4b1a9831bd9c758fad91578a54b2e156de886b3cafbfde5a886944d5bfd; exit=0; EXPECT=matched; output-sha256=b652310257965782c9693e95e3be18c06db9d2001a66ce0f1f78fd619460e572; output-bytes=151; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Yawllen\Documents\GitHub\printer-core; path=279ce660d262/39 entries

- [x] G1: Две пробы Z-bottom, запреты и восстановление состояния проходят офлайн.
  CHECK: python -B tools/tests/test_z_recovery.py
  EXPECT: Z_RECOVERY_CHECKS_PASSED
  EVIDENCE: automatic-evidence=v1; definition-sha256=6c1f3e75b7b93e14b21daf2032dd01980b59eb6a231657edfc96018c76a5e0f8; exit=0; EXPECT=matched; output-sha256=6114fbf8bce8c5e68153aa2cf0feac10023e1733a929db9593517934aecd3f00; output-bytes=144; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Yawllen\Documents\GitHub\printer-core; path=279ce660d262/39 entries

- [ ] G2: Z-bottom физически повторяем: минимум 10 проб из нескольких стартовых Z после явной потери координаты.
  EVIDENCE: pending; требуется полный CAN/MCU пакет и second_travel в окне verify_backoff_mm ± tolerance.

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
