# `tools/tests`

Локальные контрактные проверки для loader/steps и статических контрактов профиля Klipper.

## Состав

- `test_loader_contracts.ps1` — PowerShell-проверка ключевых контрактов bootstrap/runtime/verify.
- `test_klipper_eddy_contracts.ps1` — PowerShell-проверка Eddy Z-home без фиксированной Z-поправки и с PROBE-коррекцией.
- `test_klipper_parking_contracts.ps1` — PowerShell-проверка парковки `END_PRINT`/`PAUSE` и отсутствия XY-движений в `CANCEL_PRINT`.
- `test_klipper_system_capabilities_contracts.ps1` — PowerShell-проверка UI capability macro для reboot/shutdown/service commands без destructive вызовов.
- `test_moonraker_host_network_contracts.ps1` — PowerShell-проверка host network Moonraker endpoints и deploy/provisioning contract.
- `test_moonraker_update_contracts.ps1` — runnable-проверка fail-closed запрета update во время печати.
- `test_treed_shell_update_contracts.ps1` — статическая проверка atomic publish, readiness и rollback UI bundle.

## Контракт

- тесты не требуют Raspberry Pi, systemd или WSL;
- проверки статические и дополняют, но не заменяют запуск shell-скриптов на целевой Linux-системе.

## Запуск

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_loader_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_eddy_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_parking_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_system_capabilities_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_moonraker_host_network_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_moonraker_update_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_treed_shell_update_contracts.ps1"
```
