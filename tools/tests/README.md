# `tools/tests`

Офлайн-проверки loader, профиля Klipper, host-расширений и runtime updater.
Аппаратная приёмка Z/Eddy запускается отдельно по своей инструкции.

## Состав

- `test_driver_modes.py` — офлайн-модель допуска XYZ, частичных SPI-отказов и отката, сохранения, профилей обдува и доставки. Запуск `python -B tools/tests/test_driver_modes.py`; аппаратную приёмку не заменяет.

- `test_loader_progress.py` — настоящий apply-цикл на временных шагах: TTY-шкала/спиннер, сохранение логов, счётчики, required fail-fast/exit status, optional skip/failure и режим `off`. Запуск на Linux/WSL: `python3 -B tools/tests/test_loader_progress.py`; без установки пакетов, сервисов или подключения к устройству.

- `test_light_events.py` — рендер реальных Jinja-макросов: дефолты/сохранение света, wire-событие и guard филамента при печати, паузе и прочистке. Запуск `python -B tools/tests/test_light_events.py` в окружении с Jinja2; без принтера.

- `test_start_purge.py` — рендер одной purge-линии у Y-min: подача E30, длина в половину ширины стола, безопасные переезды, границы, лимит расхода, работа с нулевым PA, передняя raw-парковка и сохранение babystep при отмене и завершении. Запуск `python -B tools/tests/test_start_purge.py` в окружении с Jinja2; без принтера, фактическую запись калибровки, заполнение сопла и ширину дорожки не проверяет.

- `test_docs_consistency.py` — порядок шагов loader, локальные ссылки документации и пометка об именах релизных артефактов для совместимости. Запуск `python -B tools/tests/test_docs_consistency.py`; без устройства.

- `test_print_cancel.py` — отмена нагрева/охлаждения и остатка макроса, завершение текущего движения, cleanup и сброс сигнала для следующей задачи. Запуск `python -B tools/tests/test_print_cancel.py`; `PRINT_CANCEL_GCODE_SOURCE` задаёт путь к `gcode.py` закреплённого Klipper для проверки на его настоящем диспетчере. Без устройства.

- `test_stream_detect.py` — MJPEG, handshake, привязка ответа и отказ от устаревшего кадра; запуск `python -B tools/tests/test_stream_detect.py`. Без камеры и сервера, не оценивает модель.

- `test_treed_detection.py` — порог трёх обнаружений, сброс серии, повторы, порядок кадров, смена сессии, параллельные ответы и отказ отмены на реальном Moonraker-компоненте с mock API; адаптер `stream_detect.py` проверяется вместе с компонентом, HTTP заменён. Запуск `python -B tools/tests/test_treed_detection.py`; аппаратное выполнение не проверяет.

- `test_z_recovery.py` — одна проба DIAG, измеренный ход до Eddy Z0, ограничение Z, сброс при отказах, восстановление TMC и интеграция cfg; запуск `python -B tools/tests/test_z_recovery.py`. Это офлайн-модель, не аппаратный допуск. С `Z_RECOVERY_KLIPPER_SOURCE` дополнительно проверяет HomingMove, CoreXY и Coord закреплённого Klipper (`extras_homing.py`, `kinematics_corexy.py`, `gcode.py`).
- `test_clog_recovery.py` — один импульс энкодера за каждую из пяти попыток не подтверждает подачу; проверяет счётчик и порог без устройства. Запуск `python -B tools/tests/test_clog_recovery.py`.

- `test_sgt_calibration.py` / `test_sgt_executor.py` — поиск SGT, модель Klipper, восстановление и отказы; [контракт и запуск](../../docs/sgt-calibration.md).
- `test_loader_contracts.ps1` — PowerShell-проверка ключевых контрактов bootstrap/runtime/verify.
- `test_can_txqueue_contracts.ps1` / `test_can_txqueue_runtime.sh` — default `128` и повторное применение qlen к существующему `can0`.
- `test_klipper_eddy_contracts.ps1` — PowerShell-проверка Eddy Z-home, штатной парковки и единственного START_PRINT-сценария.
- `test_klipper_parking_contracts.ps1` — PowerShell-проверка парковки `END_PRINT`/`PAUSE` и отсутствия XY-движений в `CANCEL_PRINT`.
- `test_klipper_operation_contracts.ps1` — общий допуск сервисных операций и переходы фаз печати.
- `test_klipper_system_capabilities_contracts.ps1` — PowerShell-проверка UI capability macro для reboot/shutdown/service commands без destructive вызовов.
- `test_moonraker_host_network_contracts.ps1` — PowerShell-проверка host network Moonraker endpoints и deploy/provisioning contract.
- `test_moonraker_update_contracts.ps1` — runnable-проверка fail-closed запрета update во время печати.
- `test_treed_shell_update_contracts.ps1` — статическая проверка atomic publish, readiness и rollback UI bundle.
- `test_treed_update_service.py` — request idempotency, busy lock, durable state, UI recovery и dispatch/recovery core worker. Запуск `python -B tools/tests/test_treed_update_service.py`; без systemd и устройства.
- `test_treed_update_component.py` — быстрый POST, локальный polling, runtime manifest/capability и отказ для source-only release. Запуск `python -B tools/tests/test_treed_update_component.py`; без сети и устройства.
- `test_treed_core_update.py` — пакет, checksum/allowlist, сохранение локальных данных, полный rollback и journal recovery после частичной установки. Запуск `python -B tools/tests/test_treed_core_update.py`; services/network изолированы, symlink и Linux dir_fd требуют соответствующих возможностей среды.
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
