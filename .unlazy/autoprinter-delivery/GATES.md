# CORE-06: доставка, совместимость и диагностика

Режим Solo. Ветка dev, база 2d634ff9ad458e1549adf529e588ca345e1f6bcc.
Начальное рабочее дерево чистое. Только локальная реализация и адресные offline
проверки с подменёнными сетью и устройством. Без commit, push, deploy, SSH,
физических запусков, установки зависимостей, обучения и реальных весов.
CHECK-команд нет; проверки запускаются явно после просмотра их кода.

- [x] G1: Отдельный TODO сохраняет обязательную проверку пустых кадров, дообучение, автосъём и состояние empty.
  EVIDENCE: docs/autoprinter-todo.md requires empty negative examples to survive
  the dataset pipeline, separate train/validation/test, false-empty evaluation,
  fine-tuning, immutable model release, validated empty and finite removal cycle.
  Linked from both existing AutoPrinter documents; no runtime empty bypass added.
- [x] G2: Пакет и runtime readiness проверяют полный AutoPrinter, сохраняя совместимость с установкой без него.
  EVIDENCE: sources/load_package reject each missing AutoPrinter module; a tiny
  complete archive and legacy archive both accepted by scoped tests. Seven real
  source modules copied through package source map and imported in isolated Python
  with ML imports forbidden; disabled component creates no resources. Candidate,
  recovery and rollback readiness receive matching manifests; failed components
  rejected. Existing loader delivery and server/info verification reused as-is.
- [x] G3: Диагностика показывает успешный sync и бюджет журнала без секретов и данных прежней привязки.
  EVIDENCE: Component tests verify local version fields, successful sync timestamp,
  failure not refreshing it, revocation hiding timestamp/journal and unchanged wire
  snapshot. Configuration failure is a stable code and starts no background task.
- [x] G4: Рост SQLite ограничен, существующий журнал и данные вне пакета сохраняются.
  EVIDENCE: Page budget defaults to 64 MiB; no pruning. SQLITE_FULL test confirms
  actual database error, rollback, preserved event, released transaction, bounded
  file, subsequent pause and reopen. Lowering budget below existing size rejects
  without truncation or lock leak. Update/recovery/rollback tests retain private
  credential, journal, account G-code and external weight sentinels unchanged.
- [x] G5: Адресные проверки, просмотр diff, граф и очистка завершены; ограничения названы.
  EVIDENCE: Scoped unittest run: 75 tests in 108.297s, exit 0, OK (3 Windows
  symlink skips); two additional graph-identified execution recovery tests passed
  in 5.889s. Combined: 74 passed, 3 skipped, no failures. RuntimeWarning and
  DeprecationWarning treated as errors. In-memory syntax compile passed for all
  six changed executable/test files. Actual production diff reviewed; diff check
  passed. Graph 2026-10-18T12:42:11Z: 3796 nodes/13443 edges; all six paths have
  current metadata/no_recorded_issue. Impact lists 14 changed paths/60 symbols,
  no result pagination pending. Task temp directory inventoried empty (0 bytes),
  removed after final tests and absence checked. No full suite, release build,
  Linux/hardware, real ML run or installation. Branch dev; no Git write/deploy.
  Local baseline/rollback point is unchanged HEAD 2d634ff9ad458e1549adf529e588ca345e1f6bcc.
  SQLite limit/automatic rollback semantics checked against official pragma and
  transaction references; operational caveats documented in docs/autoprinter.md.
