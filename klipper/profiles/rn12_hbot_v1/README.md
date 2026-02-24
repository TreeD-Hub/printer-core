# Профиль `rn12_hbot_v1` (MKS Robin Nano 1.2)

Профиль описывает рабочую конфигурацию Klipper для RN12 и раскладывается loader-пайплайном в runtime (`/home/pi/printer_data/config`).

## Точка входа и include-цепочка

Точка входа: `klipper/printer.cfg`.

Текущий порядок include:
1. `profiles/rn12_hbot_v1/mcu_rn12.cfg`
2. `profiles/rn12_hbot_v1/printer_base.cfg`
3. `profiles/rn12_hbot_v1/gcode_features.cfg`
4. `profiles/rn12_hbot_v1/steppers.cfg`
5. `profiles/rn12_hbot_v1/extruder.cfg`
6. `profiles/rn12_hbot_v1/bed_heater_dc.cfg`
7. `profiles/rn12_hbot_v1/fans.cfg`
8. `profiles/rn12_hbot_v1/macros.cfg`
9. `profiles/rn12_hbot_v1/ui.cfg`
10. `local_overrides.cfg` (локальный runtime-файл на Pi)

## Макросы: публичный интерфейс и private-слой

`macros.cfg` не содержит логику и остается агрегатором include-файлов.  
Точка входа и контракт не изменены: `printer.cfg -> macros.cfg`.

Порядок модулей внутри `macros.cfg`:
1. `macros_core.cfg`
2. `macros_camera.cfg`
3. `macros_print_flow.cfg`
4. `macros_pause_resume.cfg`
5. `macros_filament.cfg`
6. `macros_utils.cfg`

### Публичные макросы (для оператора)
- `START_PRINT`
- `END_PRINT`
- `PAUSE`
- `RESUME`
- `CANCEL_PRINT`
- `LOAD_FILAMENT`
- `UNLOAD_FILAMENT`
- `TREED_SAVE_CONFIG`
- `TREED_CAM_ZOOM_PROFILE`
- `TREED_CAM_ZOOM_STATUS`
- `TREED_CAM_ZOOM_WIDE`
- `TREED_CAM_ZOOM_MEDIUM`
- `TREED_CAM_ZOOM_CLOSE`
- `M600` (определен в `gcode_features.cfg`)

### Служебные системные
- `CLEAR_PAUSE` — override штатной команды Klipper, оставлен публичным для безопасного восстановления pause-state.

### Внутренние private-макросы (не для ручного запуска)
- `_TREED_PRINT_DEFAULTS`
- `_TREED_PRINT_AREA_CFG`
- `_TREED_PRINT_OFFSET_ENABLE`
- `_TREED_PRINT_OFFSET_DISABLE`
- `_TREED_PAUSE_PARK_CFG`
- `_TREED_PAUSE_STATE`
- `_TREED_START_STATE`
- `_TREED_PAUSE_EXEC_STATE`
- `_TREED_RESUME_WIPE_STATE`
- `_TREED_CAM_STATE`
- `_TREED_CAM_TICK` (`[delayed_gcode]`)
- `_TREED_CAM_START`
- `_TREED_CAM_STOP`
- `_TREED_START_PREP_STATE`
- `_TREED_START_MACHINE_PREP`
- `_TREED_START_PREHEAT`
- `_TREED_START_POSITION_AND_FINAL_HEAT`
- `_TREED_START_PRIME`
- `_TREED_START_POST_HOOKS`
- `_TREED_PAUSE_PREP_STATE`
- `_TREED_PAUSE_EXEC`
- `_TREED_RESUME_PREP_WIPE`
- `_TREED_RESUME_HEAT_PURGE_WIPE`
- `_TREED_RESUME_FINALIZE`
- `_TREED_RESUME_POST_HOOKS`

`START_PRINT`, `PAUSE`, `RESUME` работают как тонкие оркестраторы и вызывают фазовые private-хелперы в фиксированном порядке.

Для RN12 действует fail-fast контракт: рабочие фазы `START_PRINT/PAUSE/RESUME` требуют наличие секции `[heater_bed]`.
Если секция отсутствует (поврежденный/неполный runtime-конфиг), макросы аварийно завершаются с ошибкой.

### Временные алиасы совместимости (deprecated)
| Старое имя | Новое имя | План удаления |
|---|---|---|
| `TREED_CAM_START` | `_TREED_CAM_START` | после `dev` + ближайший релиз в `main` |
| `TREED_CAM_STOP` | `_TREED_CAM_STOP` | после `dev` + ближайший релиз в `main` |

Все алиасы в таблице считаются системными и не предназначены для прямого использования оператором.

## Политика `TREED_SAVE_CONFIG`

`TREED_SAVE_CONFIG` блокируется только в двух случаях:
- идет активная печать (`print_stats.state == printing`);
- активна пауза (`pause_resume.is_paused == 1`).

В остальных состояниях (`ready/idle/complete/cancelled` и т.д.) команда разрешена.

## G-code совместимость

`gcode_features.cfg` отвечает за совместимость слайсерного G-code:
- `G2/G3` (`[gcode_arcs]`)
- `G10/G11` (`[firmware_retraction]`)
- `M486` (`[exclude_object]`)
- `M600` (обертка под текущую логику `PAUSE` + `UNLOAD_FILAMENT`)

## Контракт со слайсером

Профиль использует две системы координат:
- `print coords`: координаты слайсера (операторская модель).
- `raw coords`: физические координаты механики.

Текущий контракт зон в `raw coords`:
- сервис/перемещения: `Y=0..64`
- печать: `Y=65..245` (длина 180)

Трансляция `print coords -> raw coords` выполняется макросным `SET_GCODE_OFFSET`:
- включение: `_TREED_PRINT_OFFSET_ENABLE`
- отключение: `_TREED_PRINT_OFFSET_DISABLE`

Для `PAUSE/RESUME` действует инвариант:
- `RESUME_BASE` вызывается только после восстановления того же offset-state,
  который был сохранен на `PAUSE_BASE`.
- `PAUSE` паркует голову в raw `X5 Y5` (fallback: raw `X5 Y30`).
- purge/wipe в `RESUME` выполняются только в raw сервисной зоне (`Y<=64`).

### Управление zoom-профилем камеры

- `TREED_CAM_ZOOM_PROFILE PROFILE=wide|medium|close`
  - переключает активный zoom-профиль backend-sidecar через `machine.shell_command`;
  - не влияет на логику `_TREED_CAM_START/_TREED_CAM_STOP` и таймер снимков.
- `TREED_CAM_ZOOM_STATUS`
  - печатает текущий профиль и URL camera-контура (через runtime shell_command).
- алиасы `TREED_CAM_ZOOM_WIDE|MEDIUM|CLOSE`
  - удобные команды для UI-кнопок без передачи параметра `PROFILE`.

Ожидаемая настройка слайсера:
- origin: `X=0`, `Y=0`
- размер стола: `245 x 180`

## Локальные override

- Шаблон в репозитории: `klipper/local_overrides.example.cfg`
- Runtime-файл на устройстве: `local_overrides.cfg`
- `local_overrides.cfg` не коммитится и считается локальным source-of-truth для конкретного экземпляра принтера

## Как это раскладывает loader

1. `loader/steps/klipper-sync.sh` синхронизирует дерево `klipper/` в staging (`/home/pi/treed/klipper`)
2. `loader/steps/klipper-profiles.sh` подставляет актуальный transport/serial в `mcu_rn12.cfg`
3. `loader/steps/klipper-core.sh` раскладывает staging в runtime (`/home/pi/printer_data/config`)
