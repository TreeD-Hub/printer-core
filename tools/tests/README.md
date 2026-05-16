# `tools/tests`

Локальные контрактные проверки для loader/steps и статических контрактов профиля Klipper.

## Состав

- `test_loader_contracts.ps1` — PowerShell-проверка ключевых контрактов bootstrap/runtime/verify.
- `test_klipper_eddy_contracts.ps1` — PowerShell-проверка Eddy Z-home без фиксированной Z-поправки и с PROBE-коррекцией.
- `test_klipper_parking_contracts.ps1` — PowerShell-проверка парковки `END_PRINT`/`PAUSE` и отсутствия XY-движений в `CANCEL_PRINT`.
- `test_release_automation_contracts.ps1` — PowerShell-проверка release workflow, label sync, PR template и документации для `treed-v2_main`.

## Контракт

- тесты не требуют Raspberry Pi, systemd или WSL;
- проверки статические и дополняют, но не заменяют запуск shell-скриптов на целевой Linux-системе.

## Запуск

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_loader_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_eddy_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_parking_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_release_automation_contracts.ps1"
```
