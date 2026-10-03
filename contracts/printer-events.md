# Свет и события принтера

## Настройки света

`TREED_LIGHT_SETTINGS STARTUP=0|1 PRINT_START=0|1` сохраняет только переданные
поля в `save_variables.variables.light_on_startup` / `light_on_print_start`.
Без сохранённых значений используются `0` и `1`. Аргументы проверяются до записи.
Клиент обнаруживает поддержку через `printer.objects.list` (макрос
`gcode_macro TREED_LIGHT_SETTINGS`), подтверждает сохранение подпиской на
`save_variables`. Обычные `LIGHT_ON` / `LIGHT_OFF` независимы от этих флагов.

## Подписка на события

Профиль включает `macros_events.cfg` через `macros.cfg`. Доставка идёт штатным
копированием профиля loader-ом в `~/printer_data/config/profiles/treed_v2_corexy_v1`.
Дополнительного сервиса или Moonraker-компонента нет.

Клиент получает `notify_gcode_response` по WebSocket Moonraker. Формат сообщения:

```text
treed_event v1|12|paused|filament_runout
treed_event v1|13|clog_attempt|2/5
```

Допустим префикс `// ` от транспорта. Поля: версия, положительный `sequence`,
`code`, `detail` (может быть пустым). Текст для человека строится клиентом по
коду; формат не зависит от языка интерфейса. Температурный/отладочный RESPOND
не является уведомлением этого контракта.

Дополнительно подписаться через `printer.objects.subscribe` или запросить
`printer.objects.query` объект `gcode_macro _TREED_EVENT`: он содержит последнее
событие в полях `sequence`, `code`, `detail`. Это восстанавливает последнее
состояние после reconnect; промежуточные события за время отключения не хранятся.
`sequence` растёт до перезапуска Klipper и затем начинается с нуля. Клиент
дедуплицирует RESPOND и snapshot по sequence, сбрасывает курсор при рестарте.
История UI ограничена 50 событиями сеанса и не сохраняется после закрытия UI.

| code | detail / назначение |
| --- | --- |
| `paused` | `operator`, `filament_runout`, `filament_change` (M600), `clog` |
| `clog_started` | Начало нагрева и восстановления подачи |
| `clog_attempt` | Номер/максимум попыток, например `2/5` |
| `clog_succeeded` | Подача подтверждена, начинается возобновление |
| `clog_failed` | `low_target`, `no_filament`, `encoder_unavailable`, `no_motion`, `timeout`; остаётся пауза и цель 140 °C |
| `clog_aborted` | Процедура прервана командой управления |
| `print_preparing` | Стартовый цикл после валидации |
| `print_resumed` | Возобновление задания |
| `print_cancelled` | `operator` или `spaghetti` (детекция камеры) |
| `print_complete` | Завершён END_PRINT, включая автосъём, если он запрошен |

Ошибки Klipper, потеря связи и ошибки задания дополнительно определяются из
`webhooks`, `print_stats`, событий состояния Moonraker и ответов `!!`.
Состояние `gcode_macro _TREED_CLOG_RECOVERY_STATE.active` используется отдельно
для блокировки ручной подачи во время прочистки; уведомление не заменяет guard.
