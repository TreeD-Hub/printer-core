# `tools/tests`

Локальные контрактные проверки для loader/steps и статических контрактов профиля Klipper.

## Состав

- `test_loader_contracts.ps1` — PowerShell-проверка ключевых контрактов bootstrap/runtime/verify.
- `test_klipper_parking_contracts.ps1` — PowerShell-проверка парковки `END_PRINT`/`PAUSE` и отсутствия XY-движений в `CANCEL_PRINT`.

## Контракт

- тесты не требуют Raspberry Pi, systemd или WSL;
- проверки статические и дополняют, но не заменяют запуск shell-скриптов на целевой Linux-системе.

## Запуск

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_loader_contracts.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_parking_contracts.ps1"
```
