# `tools/tests`

Локальные контрактные проверки для loader/steps и статических контрактов профиля Klipper.

## Состав

- `test_print_cancel.py` — отмена нагрева/охлаждения и остатка макроса, завершение текущего движения, cleanup и сброс сигнала для следующей задачи. Запуск `python -B tools/tests/test_print_cancel.py`; `PRINT_CANCEL_GCODE_SOURCE` задаёт путь к `gcode.py` закреплённого Klipper для проверки на его настоящем диспетчере. Без устройства.

- `test_stream_detect.py` — MJPEG, handshake, привязка ответа и отказ от устаревшего кадра; запуск `python -B tools/tests/test_stream_detect.py`. Без камеры и сервера, не оценивает модель.

- `test_treed_detection.py` — порог трёх обнаружений, сброс серии, повторы, порядок кадров, смена сессии, параллельные ответы и отказ отмены на реальном Moonraker-компоненте с mock API; также статическая проверка регистрации/защиты макроса. Запуск `python -B tools/tests/test_treed_detection.py`; серверный транспорт и аппаратное выполнение не проверяет.

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
- `test_runtime_repo_sync.sh` — runnable-проверка exact Git sync, восстановления detached/shallow/wrong-origin checkout и сохранения dirty данных.
- `test_mainsail_bundle_offline.sh` — runnable offline deployment bundled Mainsail без обращения к GitHub.
- `test_runtime_update_contracts.ps1` — единая проверка runtime manifest, порядка host/firmware update и production fail-closed gate.

## Контракт

- тесты не требуют Raspberry Pi, systemd или WSL;
- проверки дополняют, но не заменяют запуск shell-скриптов на целевой Linux-системе.

## Запуск

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
