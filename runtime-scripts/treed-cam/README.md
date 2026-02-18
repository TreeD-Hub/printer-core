# TreeD Cam Runtime Scripts

Папка содержит runtime-скрипты камеры для интеграции Klipper/Moonraker.

## Скрипты и поведение

- `session_start.sh`
  - создает каталог новой сессии в `${PI_HOME}/treed/cam/prints`;
  - записывает путь активной сессии в marker-файл `/tmp/treed_cam_session_dir`;
  - делает стартовый снимок в best-effort режиме (`curl`-ошибка не блокирует сессию).
- `snapshot.sh`
  - проверяет наличие marker-файла сессии;
  - при активной сессии сохраняет очередной снимок в каталог сессии;
  - при отсутствии сессии/ошибке камеры завершает работу безопасно (`exit 0`).
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

- текущий snapshot endpoint захардкожен: `http://127.0.0.1:8080/?action=snapshot`;
- marker-файл сессии хранится в `/tmp` и сбрасывается после reboot;
- скрипты рассчитаны на fail-safe поведение: не должны валить основной печатный контур.
