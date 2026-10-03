# TreeD Cam Runtime Scripts

Папка содержит runtime-скрипты камеры для интеграции Klipper/Moonraker.

## Скрипты и поведение

- `cam_env.sh`
  - единый helper для runtime-переменных камеры;
  - читает optional override-файл `${PI_HOME}/treed/cam/config/runtime.env`;
  - возвращает snapshot endpoint через `TREED_CAM_SNAPSHOT_URL` (или дефолт).
- `session_start.sh`
  - создает каталог новой сессии в `${PI_HOME}/treed/cam/prints`;
  - записывает путь активной сессии в marker-файл `/tmp/treed_cam_session_dir`;
  - делает стартовый снимок в best-effort режиме;
  - при сбое snapshot пишет ограниченное предупреждение в stderr (throttled).
- `snapshot.sh`
  - проверяет наличие marker-файла сессии;
  - при активной сессии сохраняет очередной снимок в каталог сессии;
  - при отсутствии сессии/ошибке камеры завершает работу безопасно (`exit 0`) с throttled warning.
- `session_stop.sh`
  - завершает сессию удалением `/tmp/treed_cam_session_dir`;
  - идемпотентен (повторный вызов безопасен).
- `stream_detect.py`
  - диагностический клиент непрерывного MJPEG и классификатора спагетти v5;
  - использует стандартную библиотеку Python 3, без дополнительных пакетов;
  - авторизованный handshake и постоянное HTTP-соединение к API;
  - один свежий кадр в памяти, один запрос в обработке; промежуточные кадры пропускаются при отставании анализа;
  - выводит JSON с результатом, временем обработки, задержкой и числом пропущенных кадров;
  - проверяет идентификаторы, версию модели и порог ответа, повторяет handshake после отказа;
  - не пишет изображения на диск и не отправляет G-code.

## Диагностический запуск потока

Сначала запустить сервер v5 на вычислительном узле по `printer-defect-server/README.md`, затем на принтере:

```bash
export DETECT_API_KEY='<тот же ключ, что на сервере>'
python3 /home/radxa/treed/cam/bin/stream_detect.py --server http://SERVER_IP:8000 --frames 150
```

Путь runtime зависит от `PI_HOME`; файл доставляется существующим шагом `treed-cam.sh` вместе с остальными скриптами.
Без `--frames` клиент работает до Ctrl+C. Он запускается явно, отдельно от сессий печати и systemd.
`--timeout` (default 10 секунд) ограничивает сетевые операции и ожидание свежего кадра; после сбоя повтор через 2 секунды.
Неизвестный адрес сервера или отсутствие ключа блокируют запуск.

Переменные окружения: `TREED_DETECT_SERVER_URL` (вместо `--server`),
`TREED_CAM_STREAM_URL` (вместо `--stream`, default `http://127.0.0.1:8080/?action=stream`),
`DETECT_API_KEY` (обязательный общий ключ; не передаётся аргументом командной строки).
При использовании `runtime.env` экспортировать переменные в окружение Python; сам клиент этот shell-файл не читает.

Handshake: `POST /stream/hello` с `X-API-Key` и JSON `protocol_version=1`, `session_id`.
Сервер подтверждает модель, порог и лимит JPEG; после этого клиент вызывает `/classify` для каждого отправленного кадра.
HTTP 401/409/503 не считается готовностью. Каждый ответ проверяется на соответствие сессии и кадру.
Сессия диагностическая; автоматическая реакция на результат в этом запуске отключена.
Проверка: `python -B tools/tests/test_stream_detect.py` из корня репозитория.

## Входные параметры

`session_start.sh`:

- имя сессии берется из `arg1`, если не передан — из `PARAMS`;
- вход нормализуется и санитизируется до безопасного имени каталога;
- если итоговое имя пустое, используется `unknown`.

`snapshot.sh` и `session_stop.sh`:

- не требуют входных параметров.

## Контракт интеграции

- Moonraker `shell_command`:
  - `treed_cam_session_start`
  - `treed_cam_snapshot`
  - `treed_cam_session_stop`
- вызов идет из Klipper-макросов через `action_call_remote_method`.

Локальная реакция на результат анализа подготовлена в компоненте
[`treed_detection`](../../moonraker/components/README.md#назначение-treed_detectionpy).
Он использует этот marker и сохранённые JPEG, а поколение `_TREED_CAM_STATE`
защищает отмену от старых ответов. Скрипты сессии сохраняют снимки;
`stream_detect.py` отдельно передаёт видеокадры и получает результаты v5.
Преобразование этих ответов в локальный контракт отмены ещё не подключено.

## Деплой

- источник: `runtime-scripts/treed-cam/*`
- целевой путь: `${PI_HOME}/treed/cam/bin/*`
- выполняет шаг: `loader/steps/treed-cam.sh`

## Ограничения и замечания

- snapshot endpoint задается через `TREED_CAM_SNAPSHOT_URL` (optional),
  fallback: `http://127.0.0.1:8080/?action=snapshot`;
- optional runtime override-файл: `${PI_HOME}/treed/cam/config/runtime.env`;
- если `PI_HOME` не задан, домашний каталог определяется из пути установки
  скриптов `${PI_HOME}/treed/cam/bin`, одинаково для старта сессии и snapshot;
  имя пользователя `pi` не подставляется, явный `PI_HOME` имеет приоритет;
- интервал warning-throttle задается `TREED_CAM_SNAPSHOT_WARN_INTERVAL_SEC` (по умолчанию `300` сек);
- marker-файл сессии хранится в `/tmp` и сбрасывается после reboot;
- скрипты рассчитаны на fail-safe поведение: не должны валить основной печатный контур.
