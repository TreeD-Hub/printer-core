# Контракт Printer Core ↔ UI

`printer-protocol.json` фиксирует опубликованные идентификаторы и пример значений
активного профиля `treed_v2_corexy_v1`. Это fixture для проверки совместимости,
не runtime-конфиг и не перечень внутренних команд для UI.

`tools/tests/test_klipper_ui_contracts.ps1` сверяет его с
`klipper/profiles/treed_v2_corexy_v1/macros_ui_contract.cfg`.
UI CI читает этот файл из checkout `printer-core` и пропускает через свой
Moonraker normalizer. Изменять контракт и fixture следует вместе.
