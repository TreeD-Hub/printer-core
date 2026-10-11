# Компоненты Moonraker

Расширения Moonraker для команд, сети, датчика филамента, обновления,
восстановления и локальной реакции на результаты анализа камеры.
Компоненты доставляет шаг `loader/steps/moonraker-config.sh`.

## Состав

- `treed_shell_command.py`
- `treed_host_network.py`
- `treed_resonance.py`
- `treed_filament_sensor.py`
- `treed_update.py`
- `treed_recovery.py`
- `treed_detection.py`
- `treed_print_feedback.py`

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

## Назначение `treed_resonance.py`

- `GET /server/treed/resonances?mode=full|light` использует штатную авторизацию и `result` wrapper Moonraker;
- возвращает `{mode, axes: {x, y}}`; ось равна `null`, если файла нет, иначе содержит исходный `csv` и `revision` (mtime в наносекундах и SHA-256 содержимого);
- читает только `/tmp/calibration_data_x_treed_full.csv`, `/tmp/calibration_data_y_treed_full.csv`, `/tmp/calibration_data_x_treed_light_x.csv` и `/tmp/calibration_data_y_treed_light_y.csv`, которые создают штатные макросы input shaper;
- отклоняет ссылки, специальные файлы, пустой или некорректный CSV, файлы больше 2 МиБ и изменения во время чтения; ошибки доступны клиенту, отсутствующая ось не подменяется примером;
- не запускает команды и не сохраняет настройки. CSV в `/tmp` исчезают после перезагрузки; revision сравнивают до и после своего запуска, чтобы не показать прежний результат как новый.

Для live-графиков нужны этот компонент и секция `[treed_resonance]` в `base/00-core.conf`. Loader доставляет модуль и проверяет его загрузку; до обновления backend клиент сообщает о недоступном API. Формат имён и столбцов соответствует [закреплённой версии Klipper](https://github.com/Klipper3d/klipper/blob/ce7002bedf37e938bb483572949f3703ac6476cb/klippy/extras/resonance_tester.py). Офлайн-проверка чтения и API: `python -B tools/tests/test_treed_resonance.py`.

## Назначение `treed_print_feedback.py`

- `GET/POST /server/treed/print-feedback` использует штатную авторизацию Moonraker и возвращает raw `{sessionId, filename, rating}`; `rating` равен `good`, `bad` или `null`;
- GET принимает полный относительный `filename` и доступен только при совпадающем `print_stats.filename`, состоянии `complete`, выключенной камере, отсутствии активного marker и совпадающем токене runtime-камеры с `session.json`;
- `frames_root` совпадает с `${PI_HOME}/treed/cam/prints`. GET читает `.latest_session` и проверяет закрытый `session.json` и наличие сохранённых `img_*.jpg`; каталог по basename не угадывается. Старый latest не открывает оценку после новой печати того же файла, если камера не смогла обновить метаданные;
- POST принимает `{sessionId, filename, rating}` и атомарно сохраняет `rating.json` у явно выбранной закрытой сессии. Начало следующей печати не переадресует оценку на новую сессию;
- ссылки, выход из `frames_root`, неподходящий filename, незакрытая сессия и некорректный JSON возвращают видимую ошибку. Сбой записи сохраняет прежний `rating.json`;
- `/tmp/treed_cam_session_dir.feedback-unavailable` блокирует GET после сбоя начала метаданных, пока следующий успешный старт не снимет marker. Сессии, запущенные вручную без токена, можно использовать для снимков, но оценить их через этот API нельзя. Старые сессии без `session.json` также недоступны.

Компонент включён секцией `[treed_print_feedback]` в `base/00-core.conf`; loader доставляет модуль и проверяет его загрузку. Кадры остаются без преобразования. Проверка без устройства: `python -B tools/tests/test_treed_print_feedback.py`.

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
- путь назначения: `${TREED_MOONRAKER_SRC_DIR:-${PI_HOME}/moonraker}/moonraker/components`; loader сверяет его с checkout, который использует `moonraker.service`.
- деплоятся все `*.py` компоненты из этого каталога.

## Ограничения

- компонент должен оставаться совместимым с текущим API Moonraker;
- изменения компонента проверяются вместе с `moonraker/base/00-core.conf`.

Смежная документация: [конфигурация Moonraker](../README.md),
[камерные скрипты](../../runtime-scripts/treed-cam/README.md),
[обновление и откат](../../runtime-scripts/treed-update/README.md),
[офлайн-проверки](../../tools/tests/README.md).
