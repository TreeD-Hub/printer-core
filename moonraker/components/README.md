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
- `treed_autoprinter.py`
- `treed_autoprinter_execution.py`
- `treed_autoprinter_bed.py`
- `treed_autoprinter_bed_inference.py`
- `treed_vision_client.py`
- `treed_autoprinter_store.py`
- `treed_autoprinter_transport.py`

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

- `GET /server/treed/detection/status` возвращает активный `session_id`, счётчик, `analysis_state`, `pause_state`, `pause_error`, сохранённый `incident` и метаданные последнего кадра;
- `GET/POST /server/treed/detection/settings` читает и сохраняет boolean `enabled` в `~/treed/cam/config/detection.json`; по умолчанию детекция включена. Отключение сбрасывает серию и инвалидирует ожидающие ответы, повторное включение начинает новую сессию;
- `POST /server/treed/detection/result` принимает нормализованный результат для сохранённого JPEG текущей сессии;
- три последовательных результата `verdict=spaghetti` с интервалом до 6 секунд и общей длительностью до 12 секунд вызывают сохранение блокировки серии и `_TREED_DETECTION_PAUSE`; макрос повторно проверяет поколение и выполняет штатную `PAUSE REASON=ai_detection`;
- нормальный или неизвестный результат, разрыв камеры, смена worker и возобновление печати сбрасывают серию; повторы и результаты старше 6 секунд отклоняются;
- команда паузы и ограниченные повторы привязаны к тому же контексту; компонент проверяет фактическую паузу в пределах 6 секунд. Ошибка остаётся видимой в `pause_error`; автоматического возобновления нет;
- `POST /server/treed/detection/reset` с текущим `session_id` сбрасывает подтверждения после разрыва анализа;
- `POST /server/treed/detection/frame` с `session_id` и `frame_id` возвращает только последний известный JPEG этой сессии, до 8 МиБ, в `jpeg_base64`; произвольный путь прочитать нельзя.
- `GET /server/treed/detection/preview` возвращает JPEG и разметку одного кадра для вкладки «Камера AI». При простое запускает диагностический E0 и возвращает до 100 рамок в координатах исходного изображения. Во время печати использует последний свежий кадр штатного детектора, его `score`, `threshold` и `consecutive_count` (число последовательных срабатываний после обработки этого кадра), без запуска второго анализа. Без результата возвращает изображение с причиной недоступности; при выключенном AI модель не вызывается. Просмотр не создаёт доказательств пустого стола и не меняет очередь или PAUSE.

Опциональные параметры секции `[treed_detection]`: `snapshot_url` — локальный
HTTP-адрес JPEG, по умолчанию `http://127.0.0.1:8080/?action=snapshot`;
`vision_socket` — абсолютный путь к приватному сокету, по умолчанию
`~/treed/vision/run/vision.sock` в домашнем каталоге пользователя Moonraker.
Запросы просмотра сериализованы; второй одновременный запрос получает HTTP 503.

`stream_detect.py --react` передаёт JPEG локальной службе `treed-vision` через Unix-сокет и сообщает её результат компоненту Moonraker. Кадры не отправляются backend для анализа. Endpoints используют штатную авторизацию Moonraker; просмотр кадра в интерфейсе не даёт службе inference права управлять принтером. Классификатор спагетти не возвращает рамки детектора.

## Назначение `treed_autoprinter.py`

- компонент подключён в `base/00-core.conf`; очередь, локальное исполнение и проверка стола работают без фича-флагов;
- `GET /server/treed/autoprinter/status` показывает только безопасную сводку локального хранилища и состояние связи; `POST /server/treed/autoprinter/pairing` запрашивает у backend короткоживущий код для явного связывания аккаунта;
- status содержит версии протокола/конфигурации; время успешного sync и бюджет SQLite видны только при paired-состоянии. `max_journal_bytes` по умолчанию ограничивает БД 64 МиБ без удаления истории;
- `POST /server/treed/autoprinter/control` принимает только перечисленные локальные действия управления очередью и текущей печатью; endpoint не принимает запуск задания, путь файла или доказательство проверки стола;
- все три endpoint используют штатную авторизацию Moonraker; API чтения, списка и загрузки локальных файлов не регистрируется;
- `treed_autoprinter_transport.py` выполняет ограниченные HTTPS-запросы к одному настроенному origin без переходов по redirect. Credential хранится отдельно от исходников в `credentials_path` с правами владельца;
- принятые assets сохраняются через `treed_autoprinter_store.py`, receipt отправляется после локальной проверки и durable-записи. Локальный список ожидающих receipt повторяется после потери ответа backend, даже если передача больше не приходит в sync;
- `treed_autoprinter_execution.py` ведёт локальное исполнение и неизменяемый outbox событий; `treed_autoprinter_device.py` проверяет runtime и выполняет разрешённые действия через штатный Klippy API;
- executor проверяет очередь примерно раз в секунду отдельно от сетевого backoff; медленный sync временно удерживает общий lock и задерживает исполнение и status;
- недоступный адаптер принтера блокирует исполнение. Live-запуск требует квалифицированного провайдера свежей проверки пустого стола;
- диагностический кандидат E0 обращается к общей службе через `treed_vision_client.py`, но сообщает только `occupied` или `unknown`; сводка доступна только paired status и не меняет `capabilities.bed_check`;
- каталог spool ограничен 1 ГиБ с резервом 16 МиБ свободного места; при нехватке места очередь блокирует новые запуски, автоматической очистки нет;
- Для работы компонента нужны `api_origin`, `profile_id` и закрытый JSON credential. UUID берётся из credential; непустой `printer_id` в конфиге должен совпадать с ним. Постоянное имя приходит от сервера как `display_name`; существующий журнал отклоняет другой UUID. Без корректной конфигурации status сообщает `configured: false` и исполнение не запускается; credential, пути файлов и имена пользовательских файлов в нём не возвращаются.

Внутренний контракт адаптера:

```json
{"session_id":"1:0:part__20260930_120000","frame_id":"detect_0123456789abcdef0123456789abcdef_000000000001.jpg","captured_at":"2026-09-30T12:00:01Z","worker_generation":"d25971f6-fbed-4ade-8b50-b60933b85932","model_version":"spaghetti_presence_v12","verdict":"spaghetti","score":0.8}
```

`session_id` получают из status до анализа; `frame_id` — имя JPEG в текущем каталоге сессии, без пути. Ответ связан с тем же контекстом, временем захвата, поколением worker и версией модели. При разрыве анализа адаптер вызывает `/reset` и ждёт подтверждения перед новыми положительными результатами. Неизвестный результат не считается нормальным кадром и не может подтвердить дефект.

`frames_root` задаёт корень каталогов кадров; `session_file` по умолчанию `/tmp/treed_cam_session_dir`. Поколение камеры меняется при старте, паузе и остановке; ответ до паузы не применим после возобновления. Только `pause_state=confirmed` означает наблюдавшийся переход в паузу. Поле `cancel_requested=false` оставлено для совместимости, отмена по AI больше не выполняется. Локальная проверка: `python -B tools/tests/test_treed_detection.py`.

## Интеграция

- конфиг-секции объявляются в `moonraker/base/00-core.conf`;
- `[treed_host_network]` требует `network-manager`/`nmcli` на host;
- `[treed_filament_sensor]` пишет только `filament_motion_runtime.cfg`;
- `[treed_update]` требует deployed `/usr/local/sbin/treed-update-apply` и sudoers-файл из `moonraker-config.sh`;
- `[treed_recovery]` использует штатный Klippy API и не требует root/sudo;
- `[treed_detection]` связывает результат с существующим камерным контуром и защищённым макросом паузы; loader разворачивает и проверяет загрузку компонента;
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
