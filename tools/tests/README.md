# `tools/tests`

Локальные контрактные проверки для loader/steps и статических контрактов профиля Klipper.

## Состав

- `test_sgt_calibration.py` / `test_sgt_executor.py` — поиск SGT, модель Klipper, восстановление и отказы; [контракт и запуск](../../docs/sgt-calibration.md).
- `test_loader_contracts.ps1` — PowerShell-проверка ключевых контрактов bootstrap/runtime/verify.
- `test_can_txqueue_contracts.ps1` / `test_can_txqueue_runtime.sh` — default `128` и повторное применение qlen к существующему `can0`.
- `test_klipper_eddy_contracts.ps1` — PowerShell-проверка Eddy Z-home без фиксированной Z-поправки и с PROBE-коррекцией.
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
