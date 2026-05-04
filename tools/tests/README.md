# `tools/tests`

Локальные контрактные проверки для loader/steps.

## Состав

- `test_loader_contracts.ps1` — PowerShell-проверка ключевых контрактов bootstrap/runtime/verify.

## Контракт

- тесты не требуют Raspberry Pi, systemd или WSL;
- проверки статические и дополняют, но не заменяют запуск shell-скриптов на целевой Linux-системе.

## Запуск

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_loader_contracts.ps1"
```
