# Конфигурация Klipper

Папка содержит канонические конфиги Klipper для TreeD.

## Состав

- `printer.cfg` — точка входа include-цепочки;
- `printer.cfg` также содержит stock `SAVE_CONFIG`-сегмент (autosave-блок Klipper) в конце файла;
- `profiles/*` — профильные модули по конкретному железу;
- `local_overrides.example.cfg` — шаблон локальных runtime-оверрайдов.

## Установка и runtime-пути

- staging: `${PI_HOME}/treed/klipper` (шаг `loader/steps/klipper-sync.sh`)
- runtime: `${PI_HOME}/printer_data/config` (шаг `loader/steps/klipper-core.sh`)
- `canbus_uuid` и `canbus_interface` для MCU берутся напрямую из репозиторных файлов профиля (loader их не переписывает).

## Владение и сохранение калибровок

- источник истины по структуре — репозиторий, не runtime-файлы на Pi;
- `local_overrides.cfg` в runtime сохраняется между deploy-прогонами;
- `printer.cfg` в runtime деплоится из репо, но в `preserve`-режиме шаг `klipper-core.sh` возвращает сохраненный stock `SAVE_CONFIG`-сегмент;
- при активном Eddy-контуре шаг `klipper-core.sh` вычищает из сохраненного `SAVE_CONFIG` legacy `position_endstop`, старые `bltouch/probe` и сохраненные `bed_mesh`-секции;
- input shaper и откалиброванный PID хранятся в `SAVE_CONFIG`; начальные PID-поля хотэнда и стола заданы в основном `printer.cfg`, а не в include-файлах. При восстановлении autosave шаг `klipper-core.sh` комментирует начальные поля, для которых уже есть сохранённые значения;
- include-цепочка должна оставаться согласованной с активным профилем.
- для активного профиля `treed_v2_corexy_v1` Eddy Duo является обязательным Z-endstop и bed mesh контуром.

Смежная документация: [профиль принтера](profiles/treed_v2_corexy_v1/README.md),
[владение конфигами](../docs/config-ownership.md), [шаги loader](../loader/steps/README.md).
