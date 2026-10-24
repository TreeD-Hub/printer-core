# `tools/tests`

Офлайн-проверки loader, профиля Klipper, host-расширений и runtime updater.
Аппаратная приёмка Z/Eddy запускается отдельно по своей инструкции.

## Состав

- `test_light_control.py` — валидация API подсветки, отказ вне ready и единая очередь макросов/API. Для проверки настоящих классов закреплённого Klipper задать `TREED_LIGHT_KLIPPER_SOURCE` на каталог с его `output_pin.py`, `webhooks.py`, `gcode.py` и выполнить `python -B tools/tests/test_light_control.py`. MCU и реактор моделируются; это не аппаратная проверка M109/M190 или печати.

- `test_treed_autoprinter_component.py` — жизненный цикл выключенного компонента, локальный endpoint управления, закрытый credential, wire-синхронизация, default-off проверки стола, безопасная диагностика sync/журнала и независимый executor poll во время sync backoff с ожиданием задач при shutdown. Запуск `python -B tools/tests/test_treed_autoprinter_component.py`; без сети, Moonraker runtime и устройства.
- `test_treed_autoprinter_store.py` — durable-приём assets, журнал результатов, восстановление очереди и receipts, лимит SQLite с откатом переполненной записи и сохранением событий после reopen. Запуск `python -B tools/tests/test_treed_autoprinter_store.py`; без сети и устройства.
- `test_treed_autoprinter_execution.py` — durable-очередь исполнителя, восстановление, события, контекст проверки стола и запрет кандидату `empty` разрешать live-запуск или завершение. Запуск `python -B tools/tests/test_treed_autoprinter_execution.py`; с fake-устройством и без Moonraker runtime.
- `test_treed_autoprinter_device.py` — fail-closed проверка runtime, безопасная подготовка G-code и штатные Klippy API на fake-компонентах. Запуск `python -B tools/tests/test_treed_autoprinter_device.py`; без устройства.

- `test_hotend_limit.py` — нормализация целей хотэнда и PID, нижний предел,
  выключение, неконечные числа, отдельный аварийный порог и доставка расширения.
  Запуск `python -B tools/tests/test_hotend_limit.py`; без устройства.
  `HOTEND_KLIPPER_SOURCE` с каталогом `heaters.py`, `pid_calibrate.py` и
  `extruder.py` из закреплённого Klipper включает проверку штатных обработчиков;
  аппаратные объекты и ожидание температуры заменены моделью.

- `test_treed_calibration.py` — XY geometry/idle guards, атомарная запись overrides,
  height stamp после restart, подача/cleanup и сохранение rotation_distance.
  Запуск `python -B tools/tests/test_treed_calibration.py`; без Klipper и устройства.

- `test_driver_modes.py` — офлайн-модель ранней загрузки полного quiet-профиля, согласования MRES, допуска XYZ, сохранения/перезапуска и восстановления при отказах, частичных SPI-отказов, профилей обдува и доставки. Проверяет возврат quiet-лимитов после homing, включая ошибку. Запуск `python -B tools/tests/test_driver_modes.py`; аппаратную приёмку не заменяет.

- `test_loader_progress.py` — настоящий apply-цикл на временных шагах: TTY-шкала/спиннер, сохранение логов, счётчики, required fail-fast/exit status, optional skip/failure и режим `off`. Запуск на Linux/WSL: `python3 -B tools/tests/test_loader_progress.py`; без установки пакетов, сервисов или подключения к устройству.

- `test_light_events.py` — рендер реальных Jinja-макросов: дефолты/сохранение света, wire-событие и guard филамента при печати, паузе и прочистке. Запуск `python -B tools/tests/test_light_events.py` в окружении с Jinja2; без принтера.

- `test_ui_service_mode.py` — рендер сервисной парковки, центра сопла из пределов осей и запретов до движения. Запуск `python -B tools/tests/test_ui_service_mode.py` в окружении с Jinja2; без устройства, нижний DIAG и физическую парковку не проверяет.

- `test_start_purge.py` — рендер раннего нагрева, ожидания у скребка, чистки при 175 °C (пять циклов X40..100/Z0,15..0,5 с половиной скорости и ускорения), восстановления лимитов после отмены и порядка повторного замера Z после прогрева стола. Проверяет `MESH=0/1`, одну purge-линию у Y-min с E30, границы и лимит расхода, нулевой PA, переднюю raw-парковку, сохранение babystep, полную область печати из пределов осей и независимые границы сканирования Eddy. Запуск `python -B tools/tests/test_start_purge.py` в окружении с Jinja2; без принтера, фактическую запись калибровки, контакт со скребком, заполнение сопла и ширину дорожки не проверяет.

- `test_docs_consistency.py` — порядок шагов loader, локальные ссылки документации и пометка об именах релизных артефактов для совместимости. Запуск `python -B tools/tests/test_docs_consistency.py`; без устройства.

- `test_print_cancel.py` — отмена нагрева/охлаждения и остатка макроса, завершение текущего движения, cleanup, сброс сигнала и применение Z-поправки к действующей калибровке Eddy без перезапуска. Проверяет знак, накопление поправок, запрет во время печати и отказ без изменения рабочей кривой при ошибке подготовки записи. Запуск `python -B tools/tests/test_print_cancel.py`; `PRINT_CANCEL_GCODE_SOURCE` задаёт путь к `gcode.py` закреплённого Klipper, `PRINT_CANCEL_EDDY_SOURCE` — к `extras/probe_eddy_current.py` для проверки на настоящих классах калибровки и интерполяции. Без устройства и записи printer.cfg.

- `test_stream_detect.py` — MJPEG, handshake, привязка ответа и отказ от устаревшего кадра; запуск `python -B tools/tests/test_stream_detect.py`. Без камеры и сервера, не оценивает модель.

- `test_treed_detection.py` — порог трёх обнаружений, сброс серии, повторы, порядок кадров, смена сессии, параллельные ответы и отказ отмены на реальном Moonraker-компоненте с mock API; адаптер `stream_detect.py` проверяется вместе с компонентом, HTTP заменён. Запуск `python -B tools/tests/test_treed_detection.py`; аппаратное выполнение не проверяет.

- `test_z_recovery.py` — одна проба DIAG, измеренный ход до Eddy Z0, ограничение Z, сброс при отказах, восстановление TMC и интеграция cfg; запуск `python -B tools/tests/test_z_recovery.py`. Это офлайн-модель, не аппаратный допуск. С `Z_RECOVERY_KLIPPER_SOURCE` дополнительно проверяет HomingMove, CoreXY и Coord закреплённого Klipper (`extras_homing.py`, `kinematics_corexy.py`, `gcode.py`).
- `test_clog_recovery.py` — один импульс энкодера за каждую из пяти попыток не подтверждает подачу; проверяет счётчик и порог без устройства. Запуск `python -B tools/tests/test_clog_recovery.py`.

- `test_sgt_calibration.py` / `test_sgt_executor.py` — поиск SGT, модель Klipper, восстановление и отказы; [контракт и запуск](../../docs/sgt-calibration.md).
- `test_loader_contracts.ps1` — PowerShell-проверка ключевых контрактов bootstrap/runtime/verify.
- `test_managed_loader.py` — адресные shell-проверки управляемого перехода updater к loader: реестр шагов, сохранение пользовательской конфигурации и отсутствие конкурирующего baseline. Запуск `python3 -B tools/tests/test_managed_loader.py`; требуется Bash, runtime-службы не запускаются.
- `test_klipper_preserve.py` — сохранение локальных настроек и SAVE_CONFIG, отказ до очистки при ошибке снимка, сохранность копии при ошибке восстановления. Запуск `python -B tools/tests/test_klipper_preserve.py`; требуется Bash, работа идёт во временных каталогах без устройства.
- `test_update_admission.py` — допуск обновления в реальных Jinja-макросах и запрет новых запусков печати при занятом допуске. Запуск `python -B tools/tests/test_update_admission.py` в окружении с Jinja2; без устройства.
- `test_treed_loading_screen.py` — показ существующей темы TreeD, владение Plymouth и восстановление интерфейса после ошибки; системные команды заменяются заглушками. Запуск `python3 -B tools/tests/test_treed_loading_screen.py`.
- `test_can_txqueue_contracts.ps1` / `test_can_txqueue_runtime.sh` — default `128` и повторное применение qlen к существующему `can0`.
- `test_klipper_eddy_contracts.ps1` — PowerShell-проверка Eddy Z-home, штатной парковки и единственного START_PRINT-сценария.
- `test_klipper_parking_contracts.ps1` — PowerShell-проверка парковки `END_PRINT`/`PAUSE` и отсутствия XY-движений в `CANCEL_PRINT`.
- `test_klipper_operation_contracts.ps1` — общий допуск сервисных операций и переходы фаз печати.
- `test_klipper_system_capabilities_contracts.ps1` — PowerShell-проверка UI capability macro для reboot/shutdown/service commands без destructive вызовов.
- `test_moonraker_host_network_contracts.ps1` — PowerShell-проверка host network Moonraker endpoints и deploy/provisioning contract.
- `test_moonraker_update_contracts.ps1` — runnable-проверка fail-closed запрета update во время печати.
- `test_treed_shell_update_contracts.ps1` — статическая проверка atomic publish, readiness и rollback UI bundle.
- `test_loader_optimizations.ps1` — runnable-проверка кэша APT-индекса и очистки webcam-конфигов на временных fixtures.
- `test_treed_update_service.py` — request idempotency, busy lock, durable state, UI recovery и dispatch/recovery core worker. Запуск `python -B tools/tests/test_treed_update_service.py`; без systemd и устройства.
- `test_treed_update_component.py` — быстрый POST, локальный polling, runtime manifest/capability и отказ для source-only release. Запуск `python -B tools/tests/test_treed_update_component.py`; без сети и устройства.
- `test_treed_core_update.py` — пакет, checksum/allowlist, полнота AutoPrinter и загрузка доставленных модулей без ML, сохранение приватных данных, полный rollback и journal recovery после частичной установки. Проверяет подтверждённый допуск до воздействия на сервисы, readiness по manifest и отказ при failed-компоненте. Запуск `python -B tools/tests/test_treed_core_update.py`; services/network изолированы, symlink и Linux dir_fd требуют соответствующих возможностей среды.
- `check_moonraker_startup.py` — запуск конфигов и модулей из runtime ZIP на настоящем Moonraker из `runtime-versions.env`. Проверяет все обязательные loader-компоненты, стабильную readiness, API, совместимость старого конфига без `vision_socket` и отказ при отсутствующем `frames_root`. Требует Linux, отдельный checkout закреплённого Moonraker и его Python-зависимости; Klippy заменён локальным имитатором, оборудование и systemd не используются. Команда: `python -B tools/tests/check_moonraker_startup.py --moonraker-source /tmp/moonraker --archive /tmp/treed-core-runtime.zip`. Проверка обязательна в обоих CI workflows после полного offline-набора; в `run_offline.py` не входит, поскольку требует upstream runtime.
- `test_runtime_repo_sync.sh` — runnable-проверка exact Git sync, восстановления detached/shallow/wrong-origin checkout и сохранения dirty данных.
- `test_mainsail_bundle_offline.sh` — runnable offline deployment bundled Mainsail без обращения к GitHub.
- `test_runtime_update_contracts.ps1` — единая проверка runtime manifest, порядка host/firmware update и production fail-closed gate.

## Контракт

- PowerShell- и часть Python-проверок не требуют устройства или systemd; shell-проверкам и `test_loader_progress.py` нужен Bash, а некоторым проверкам updater — Linux-возможности файловой системы;
- проверки дополняют, но не заменяют запуск shell-скриптов на целевой Linux-системе.

`z_acceptance.py` выполняет серии на устройстве через диагностический сборщик.
Это отдельная процедура с движениями, а не обычный офлайн-тест; предусловия
и команды приведены в [приёмке Z/Eddy](../../docs/z-eddy-acceptance.md).

## Запуск

Если Jinja2 отсутствует в текущем Python, при установленном `uv` purge-тест можно запустить в изолированном окружении. При первом запуске `uv` загрузит зависимости; файлы проекта и пакеты текущего Python не меняются:

```powershell
uv run --no-project --with jinja2 python -B tools/tests/test_start_purge.py
```

Полный offline набор: `python -B tools/tests/run_offline.py` (Python, `pwsh` 7,
Bash; на Windows предпочтён Git Bash). `test_z_acceptance.py` проверяет mesh
persistence, ошибки/реентерабельность, численные метрики и evidence на mocks;
`test_z_recovery.py` — bounded recovery. Эти проверки не закрывают hardware gates.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_loader_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_eddy_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_parking_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_operation_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_system_capabilities_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_moonraker_host_network_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_moonraker_update_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_treed_shell_update_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_runtime_update_contracts.ps1"
```
