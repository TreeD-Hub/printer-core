# Klipper Config Layer

Папка содержит канонические конфиги Klipper для TreeD.

Назначение:
- `printer.cfg` — точка входа include-цепочки;
- `printer.cfg` также содержит stock `SAVE_CONFIG`-сегмент (autosave-блок Klipper) в конце файла;
- `profiles/*` — профильные модули по конкретному железу;
- `local_overrides.example.cfg` — шаблон локальных runtime-оверрайдов.

Деплой:
- staging: `/home/pi/treed/klipper` (шаг `loader/steps/klipper-sync.sh`)
- runtime: `/home/pi/printer_data/config` (шаг `loader/steps/klipper-core.sh`)
- подстановка идентификаторов main/CAN MCU: `loader/steps/klipper-profiles.sh`

Важно:
- источник истины по структуре — репозиторий, не runtime-файлы на Pi;
- `local_overrides.cfg` в runtime сохраняется между deploy-прогонами;
- `printer.cfg` в runtime деплоится из репо, но в `preserve`-режиме шаг `klipper-core.sh` возвращает сохраненный stock `SAVE_CONFIG`-сегмент;
- при активном Eddy-контуре шаг `klipper-core.sh` вычищает из сохраненного `SAVE_CONFIG` legacy `position_endstop`, старые `bltouch/probe` и сохраненные `bed_mesh`-секции;
- include-цепочка должна оставаться согласованной с активным профилем.
