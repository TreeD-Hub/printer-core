# Профиль `rn12_hbot_v1` (MKS Robin Nano 1.2)

Этот профиль описывает рабочую конфигурацию Klipper для TreeD и разворачивается активным loader-пайплайном.

## Зачем конфиг разбит на несколько файлов

Раньше все обычно лежит в одном большом `printer.cfg`, из-за чего сложно:
- быстро найти нужный параметр;
- безопасно менять один узел без риска зацепить другой;
- понимать границы ответственности.

В этом профиле выбран модульный подход:
- один файл = одна зона ответственности;
- базовая точка входа остается единой (`klipper/printer.cfg`);
- локальные правки устройства отделены от репозитория.

Отдельно важно:
- параметры осей X/Y/Z (моторы, концевики, границы) собраны в одном файле `steppers.cfg`;
- разнесение одной оси по нескольким файлам не используется.

## Точка входа и порядок include

Точка входа: `klipper/printer.cfg`.

Текущая include-цепочка для профиля:
1. `mcu_rn12.cfg`
2. `printer_base.cfg`
3. `steppers.cfg`
4. `extruder.cfg`
5. `bed_heater_dc.cfg`
6. `fans.cfg`
7. `macros.cfg`
8. `ui.cfg`
9. `local_overrides.cfg` (локальный runtime-файл на Pi)

## Карта файлов профиля

- `mcu_rn12.cfg` — подключение к MCU (serial + restart method, transport `usb|uart`).
- `printer_base.cfg` — кинематика и базовые лимиты принтера.
- `steppers.cfg` — полные параметры осей X/Y/Z.
- `extruder.cfg` — экструдер, хотэнд, PID и ограничения подачи.
- `bed_heater_dc.cfg` — нагрев стола (DC), лимиты и verify_heater.
- `fans.cfg` — вентиляторы.
- `macros.cfg` — пользовательские макросы печати и камеры.
- `ui.cfg` — интерфейсные блоки для Mainsail/Fluidd.
- `filament_sensor.cfg` — опциональный шаблон датчика филамента (по умолчанию не подключен).

## Как это разворачивается loader-ом

1. `loader/steps/klipper-sync.sh` копирует `klipper/` в staging: `/home/pi/treed/klipper`.
2. `loader/steps/klipper-profiles.sh` подставляет актуальный `serial:` в `mcu_rn12.cfg` по `TREED_MCU_TRANSPORT`.
3. `loader/steps/klipper-core.sh` раскладывает дерево в runtime: `/home/pi/printer_data/config`.

## Локальные оверрайды

- Шаблон в репозитории: `klipper/local_overrides.example.cfg`.
- Рабочий локальный файл на Pi: `local_overrides.cfg`.
- `local_overrides.cfg` не коммитится и сохраняется при deploy шагом `klipper-core`.

## Источник истины

Если описание где-то расходится, верить в таком порядке:
1. `loader/loader.sh`
2. `docs/config-ownership.md`
3. `klipper/printer.cfg`
