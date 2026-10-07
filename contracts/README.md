# Контракт Printer Core ↔ UI

[`printer-protocol.json`](printer-protocol.json) фиксирует опубликованные идентификаторы и пример значений
активного профиля `treed_v2_corexy_v1`. Это fixture для проверки совместимости,
не runtime-конфиг и не перечень внутренних команд для UI.

[`test_klipper_ui_contracts.ps1`](../tools/tests/test_klipper_ui_contracts.ps1) сверяет его с
[`macros_ui_contract.cfg`](../klipper/profiles/treed_v2_corexy_v1/macros_ui_contract.cfg).
UI CI читает этот файл из checkout `printer-core` и пропускает через свой
Moonraker normalizer. Изменять контракт и fixture следует вместе.

Дополнительный обратно совместимый [контракт событий и света](printer-events.md)
обнаруживается по наличию макросов; обязательную версию `1.0` не меняет.

Проверку запускайте из корня репозитория:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "tools/tests/test_klipper_ui_contracts.ps1"
```
