# UI runtime tune contract

Этот документ фиксирует публичный контракт между TreeD Shell и Klipper-профилем `treed_v2_corexy_v1`.
UI не должен слать raw `M220`, `M221`, `SET_VELOCITY_LIMIT`, `SET_PRESSURE_ADVANCE`, `SET_RETRACTION` или `SET_GCODE_OFFSET`; для live-тюнинга использовать только команды ниже.

## Выполнение команд

Команды вызываются через Moonraker JSON-RPC `printer.gcode.script`:

```json
{"jsonrpc":"2.0","method":"printer.gcode.script","params":{"script":"TREED_UI_SET_SPEED_FACTOR PERCENT=120"},"id":1}
```

Все команды ниже разрешены только во время `print_stats.state = "printing"` / `"paused"` или при `pause_resume.is_paused = true`.
При нарушении контракта макрос вызывает Klipper command error через `action_raise_error(...)`; Moonraker вернет ошибку вызова `printer.gcode.script` с текстом, начинающимся с имени `TREED_UI_*`.
При успехе макрос пишет короткий `RESPOND PREFIX="treed_ui"`, но UI после команды должен перечитать state surface, а не парсить этот текст.

## Публичные команды

| Команда | Параметры | Диапазон | Во время `printing` | Во время `paused` | После успеха перечитать |
| --- | --- | --- | --- | --- | --- |
| `TREED_UI_SET_SPEED_FACTOR` | `PERCENT` | `10..300` | да | да | `gcode_move.speed_factor` |
| `TREED_UI_SET_FLOW_FACTOR` | `PERCENT` | `50..150` | да | да | `gcode_move.extrude_factor` |
| `TREED_UI_SET_ACCEL` | `ACCEL` | `500..printer.max_accel` (`25000` в профиле) | да | да | `toolhead.max_accel`, `toolhead.max_velocity` |
| `TREED_UI_SET_PRESSURE_ADVANCE` | `ADVANCE` | `0..0.20` | да | да | `extruder.pressure_advance` |
| `TREED_UI_SET_RETRACTION` | `RETRACT_LENGTH` | `0..5.0` мм | да | да | `firmware_retraction.retract_length` |
| `TREED_UI_BABYSTEP` | `DELTA` | `-0.05..0.05` мм за команду, `-1.0..1.0` мм суммарно | да | да | `gcode_move.homing_origin.z`, `gcode_macro _TREED_UI_TUNE_STATE.applied_babystep` |
| `TREED_UI_ADJUST_Z_OFFSET` | `DELTA` | alias для `TREED_UI_BABYSTEP` | да | да | те же поля, что для `TREED_UI_BABYSTEP` |

Примеры:

```gcode
TREED_UI_SET_SPEED_FACTOR PERCENT=120
TREED_UI_SET_FLOW_FACTOR PERCENT=97
TREED_UI_SET_ACCEL ACCEL=12000
TREED_UI_SET_PRESSURE_ADVANCE ADVANCE=0.075
TREED_UI_SET_RETRACTION RETRACT_LENGTH=0.9
TREED_UI_BABYSTEP DELTA=-0.02
```

## State surface

UI должен читать состояние через Moonraker `printer.objects.query` / подписку на те же объекты:

```json
{
  "jsonrpc": "2.0",
  "method": "printer.objects.query",
  "params": {
    "objects": {
      "gcode_move": ["speed_factor", "extrude_factor", "homing_origin"],
      "toolhead": ["max_velocity", "max_accel"],
      "extruder": ["temperature", "target", "pressure_advance"],
      "heater_bed": ["temperature", "target"],
      "firmware_retraction": ["retract_length"],
      "gcode_macro _TREED_UI_TUNE_STATE": ["contract_version", "applied_babystep"]
    }
  },
  "id": 2
}
```

Единицы:
- `gcode_move.speed_factor` и `gcode_move.extrude_factor` приходят дробью: `1.0 = 100%`;
- `toolhead.max_velocity` — мм/с;
- `toolhead.max_accel` — мм/с^2;
- `extruder.pressure_advance` — секунды;
- `firmware_retraction.retract_length` — мм;
- `gcode_move.homing_origin.z` — текущий live Z-offset;
- `gcode_macro _TREED_UI_TUNE_STATE.applied_babystep` — накопленный Z-delta, примененный именно через `TREED_UI_BABYSTEP` / `TREED_UI_ADJUST_Z_OFFSET`;
- `extruder.target` и `heater_bed.target` — target температуры сопла и стола.

## Не входит в MVP

`pause at layer` не публикуется как одна кнопка в MVP. Надежный контракт требует layer-change соглашения со слайсером (`SET_PRINT_STATS_INFO CURRENT_LAYER=...` или отдельный layer macro) либо предварительной обработки G-code.

`volumetric flow` не публикуется как live-регулировка: в Klipper это не универсальный runtime control. Для MVP UI должен скрыть этот control или показывать read-only расчет. Если нужен управляемый TreeD-лимит, его надо заводить отдельным pre-print preset/contract до старта печати.
