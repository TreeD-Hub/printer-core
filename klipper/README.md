# Klipper Config Layer

Папка содержит канонические конфиги Klipper для TreeD.

Назначение:
- `printer.cfg` — точка входа include-цепочки;
- `profiles/*` — профильные модули по конкретному железу;
- `local_overrides.example.cfg` — шаблон локальных оверрайдов для Pi.

Деплой:
- staging: `/home/pi/treed/klipper` (шаг `loader/steps/klipper-sync.sh`)
- runtime: `/home/pi/printer_data/config` (шаг `loader/steps/klipper-core.sh`)
- подстановка MCU serial: `loader/steps/klipper-profiles.sh`

Важно:
- источник истины по структуре — репозиторий, не runtime-файлы на Pi;
- `local_overrides.cfg` в runtime сохраняется между deploy-прогонами;
- include-цепочка должна оставаться согласованной с активным профилем.

