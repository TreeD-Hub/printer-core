# Gates: оценка завершённой печати

Mode: Orchestrated. Два репозитория: printer-core и printer-ui. Итоговая интеграция и приёмка основным агентом; оставшаяся реализация и независимое ревью выполнены Luna по запросу пользователя.
Scope: окно после успешной печати, оценка хорошо/плохо в JSON сессии камеры, повтор штатной печати и закрытие.

- [x] G1: Сценарий окна связан с переходом активной печати в complete, закрытие не открывает его повторно, отмена не предлагает оценку.
  EVIDENCE: printer-ui/src/printSession/PrintCompletionFlow.test.tsx; проверены начальное complete, отмена/ошибка, offline, дубликат complete и новая печать того же файла. Файл: 22 пройденных теста в общем адресном запуске.

- [x] G2: API сохраняет good/bad только в выбранной закрытой сессии камеры; чужие пути, ссылки и неподходящие значения отклоняются.
  EVIDENCE: tools/tests/test_treed_print_feedback.py — exit 0, 13 passed, 1 skipped (Windows не разрешает создание symlink). Проверены immutable sessionId, токен текущей печати, прежний latest, разные каталоги одного basename, открытая сессия, отсутствующие кадры, неверные параметры и сбой атомарной замены. Symlink boundary просмотрена статически, тест на ссылки локально не выполнен.

- [x] G3: Повтор использует исходный путь G-code, действующие guards команды start и подтверждение освобождённого стола; ошибки сохранения и запуска видны.
  EVIDENCE: App.tsx передаёт start через существующий executeCommand. Flow использует относительный print_stats.filename, а не абсолютный virtual_sdcard.file_path. Flow/modal/client/controller: 58 passed; регрессия пути после устранения предупреждения act дополнительно прошла адресно. Проверены table-clear, double tap, command failure, retry сохранения и временного HTTP 409. Независимое ревью Luna подтвердило устранение ошибки пути.

- [x] G4: Доставка компонента и метаданных сессии включена в существующий loader; сохранены чужие изменения, документация соответствует фактическим путям.
  EVIDENCE: Статически сверены loader/steps/treed-cam.sh, moonraker-config.sh, base/00-core.conf и камерные callers. Новые Python-файлы доставляются существующим копированием каталогов. README камеры, компонента, профиля и UI описывают session.json/rating.json и legacy-сессии без оценки. Чужие resonance/calibration изменения сохранены; итоговый diff просмотрен.

- [x] G5: Доступные адресные проверки пройдены; пределы офлайн-проверки отделены от устройства и рендера.
  EVIDENCE: UI 58/58; Python 13 passed, 1 skipped; Bash -n изменённых start/stop и loader — exit 0; tools/tests/test_klipper_ui_contracts.ps1 — PASS; git diff --check — exit 0. detect_changes выполнен в обоих репозиториях, impacted_symbols пуст из-за покрытия индекса и не заменяет чтение исходников. Linux shell-fixture test_treed_cam_paths локально не выполнена успешно из-за Windows/WSL/MSYS mapping. Full suite, build, рендер, доставка и печать на устройстве не выполнялись.

Все gates требуют ручной сверки интеграции: офлайн-тесты не доказывают фактический runtime и доставку на устройство. CHECK-команды в ledger отсутствуют.
