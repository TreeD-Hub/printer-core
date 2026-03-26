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

## Деплой

- источник: `runtime-scripts/treed-cam/*`
- целевой путь: `${PI_HOME}/treed/cam/bin/*`
- выполняет шаг: `loader/steps/treed-cam.sh`

## Ограничения и замечания

- snapshot endpoint задается через `TREED_CAM_SNAPSHOT_URL` (optional),
  fallback: `http://127.0.0.1:8080/?action=snapshot`;
- optional runtime override-файл: `${PI_HOME}/treed/cam/config/runtime.env`;
- интервал warning-throttle задается `TREED_CAM_SNAPSHOT_WARN_INTERVAL_SEC` (по умолчанию `300` сек);
- marker-файл сессии хранится в `/tmp` и сбрасывается после reboot;
- скрипты рассчитаны на fail-safe поведение: не должны валить основной печатный контур.
