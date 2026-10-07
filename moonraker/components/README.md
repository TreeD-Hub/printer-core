# Moonraker Components

Каталог для кастомных компонентов Moonraker, которые деплоятся loader.

## Состав

- `treed_shell_command.py`
- `treed_host_network.py`
- `treed_filament_sensor.py`
- `treed_update.py`
- `treed_recovery.py`
- `treed_detection.py`

## Назначение `treed_shell_command.py`

- читает секции `[shell_command <name>]` из Moonraker-конфига;
- регистрирует remote method `machine.shell_command`;
- выполняет команды асинхронно через стандартный Moonraker `shell_command` factory;
- безопасно экранирует параметры (`shlex.quote`) перед запуском.

## Назначение `treed_host_network.py`

- регистрирует endpoints Wi-Fi управления для TreeD Shell:
  - `GET /server/treed/network/status`
  - `POST /server/treed/network/scan`
  - `POST /server/treed/network/connect`
  - `POST /server/treed/network/forget`
- вызывает `nmcli` асинхронно через `asyncio.create_subprocess_exec`;
- `scan` ожидает завершения `nmcli --rescan yes`, сохраняет UTF-8 SSID и исключает скрытые сети без имени;
- возвращает raw `HostNetworkStatus` без Moonraker `result` wrapper;
- не содержит UI-правила фильтрации, сортировки или выбора сети.

## Назначение `treed_filament_sensor.py`

- регистрирует `GET/POST /server/treed/filament-sensor/settings`;
- принимает только `low | medium | high` и атомарно обновляет runtime config;
- блокирует изменение чувствительности во время активной печати;
- после успешной записи отправляет Klipper controlled `RESTART`.

## Назначение `treed_update.py`

- регистрирует endpoints обновлений для TreeD Shell:
  - `GET /server/treed/update/status`
  - `GET /server/treed/update/firmware`
  - `POST /server/treed/update/check`
  - `POST /server/treed/update/apply`
- проверяет release data отдельно для `treed-shell` и `printer-core`;
- `POST /apply` принимает `requestId` (UUID), `targetId`, `targetTag` и быстро передаёт операцию через ограниченную root-команду; клиенты без `requestId` остаются совместимы и получают серверный UUID.
- идемпотентный root-worker сериализует запросы, сохраняет операцию/историю в `/var/lib/treed-update/state.json` и предоставляет быстрый `GET /status` для восстановления после перезагрузки страницы или службы;
- `GET /status` оставляет прежние `busy`, `canApply`, `releaseResults` и добавляет `operation`, `latestOperation`, `history`, `status`, `phase`, `progress`, `resultCode`;
- release check выполняет оба запроса параллельно с таймаутом 7 секунд на источник; firmware inventory остаётся отдельным `GET /firmware`;
- UI release `ui-main-<run>-<attempt>` обновляется сервисом с readiness check и возвратом на предыдущий bundle при сбое;
- core release `vX.Y.Z` требует `treed-core-runtime.zip` с SHA-256, установленный `treed-core-update` и ownership baseline. Он обновляет наши конфиги, модули и скрипты с rollback; версия читается из подтверждённого runtime manifest. Системное A/B обновление остаётся отдельным недоступным контуром.
- раздельно сообщает expected Klipper SHA, host checkout, running Klippy, build checksums и live `mcu_version` каждой платы;
- сохраняет последнее успешное наблюдение MCU только как `lastKnown.stale=true`, если Klippy/MCU недоступны;
- сверка `mcu_version` не называется readback или криптографической проверкой прошитого бинарника.

## Назначение `treed_recovery.py`

- регистрирует `GET /server/treed/recovery/status`, `POST /start` и `POST /cancel`;
- выполняет ровно один явно запрошенный `FIRMWARE_RESTART`;
- после `ready` наблюдает required MCU 60 секунд и проверяет свежесть `bytes_read`;
- сохраняет имя потерянной MCU, CAN/serial counter deltas и историю предыдущих причин;
- не отправляет motion/heater G-code и не запускает повторные recovery.

## Назначение `treed_detection.py`

- `GET /server/treed/detection/status` возвращает активный `session_id`, счётчик и статус запроса отмены;
- `GET/POST /server/treed/detection/settings` читает и сохраняет boolean `enabled` в `~/treed/cam/config/detection.json`; по умолчанию детекция включена. Отключение сбрасывает серию и инвалидирует ожидающие ответы, повторное включение начинает новую сессию;
- `POST /server/treed/detection/result` принимает нормализованный результат для сохранённого JPEG текущей сессии;
- три последовательных `critical=true` для `defect=spaghetti` вызывают `_TREED_DETECTION_CANCEL`, который проверяет поколение сессии и выполняет штатный `CANCEL_PRINT`;
- нормальный кадр, неизвестный результат или другой класс сбрасывает серию; повторы и запоздалые кадры отклоняются;
- отмена запрашивается один раз; ошибка передачи команды остаётся в `cancel_error`, автоматического возобновления или повтора команды нет.

`stream_detect.py --react` отправляет JPEG классификатору и передаёт ответ локальному компоненту. Endpoints используют штатную авторизацию Moonraker и остаются доступными только локальному UI и адаптеру принтера через действующий nginx-контракт.

Внутренний контракт адаптера:

```json
{"session_id":"1:0:part__20260930_120000","frame_id":"img_20260930_120001_123.jpg","critical":true,"defect":"spaghetti"}
```

`session_id` получают из status перед отправкой соответствующего кадра; `frame_id` — имя JPEG в текущем каталоге сессии, без пути. Ответ привязывают к этим же идентификаторам, даже если за время анализа началось новое задание. `critical=false` означает отсутствие критического дефекта, `critical=null` — сбой или отсутствие достоверного результата. Для текущего единственного класса `defect` по умолчанию равен `spaghetti`; это внутреннее обозначение, а не научное название дефекта. Нельзя подавать строки `"true"`/`"false"` или числа вместо boolean. Адаптер должен выдавать результаты последовательно по времени сохранения кадров; каждый кадр учитывается один раз. Отсутствующий callback сам по себе не сообщает компоненту о таймауте: `stream_detect.py --react` передаёт `critical=null` при разрыве серии и ждёт подтверждения сброса перед новыми положительными результатами.

`frames_root` задаёт корень каталогов кадров; `session_file` по умолчанию `/tmp/treed_cam_session_dir`. Поколение камеры меняется при старте, паузе и остановке; ответ до паузы не применим после возобновления. `cancel_requested` подтверждает запрос команды, а не аппаратное завершение отмены; последнее проверяется по состоянию принтера. Локальная проверка: `python -B tools/tests/test_treed_detection.py`.

## Интеграция

- конфиг-секции объявляются в `moonraker/base/00-core.conf`;
- `[treed_host_network]` требует `network-manager`/`nmcli` на host;
- `[treed_filament_sensor]` пишет только `filament_motion_runtime.cfg`;
- `[treed_update]` требует deployed `/usr/local/sbin/treed-update-apply` и sudoers-файл из `moonraker-config.sh`;
- `[treed_recovery]` использует штатный Klippy API и не требует root/sudo;
- `[treed_detection]` связывает результат с существующим камерным контуром и защищённым макросом отмены; loader разворачивает и проверяет загрузку компонента;
- текущие команды используются для camera runtime:
  - `treed_cam_session_start`
  - `treed_cam_snapshot`
  - `treed_cam_session_stop`
- скрипты команд лежат в `runtime-scripts/treed-cam/*`.

## Деплой

- выполняет `loader/steps/moonraker-config.sh`;
- путь назначения определяется автоматически по фактической установке Moonraker
  (process path -> systemd unit -> типовые пути -> fallback поиск).
- деплоятся все `*.py` компоненты из этого каталога.

## Ограничения

- компонент должен оставаться совместимым с текущим API Moonraker;
- изменения компонента проверяются вместе с `moonraker/base/00-core.conf`.
