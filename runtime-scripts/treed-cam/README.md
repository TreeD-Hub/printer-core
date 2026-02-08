# TreeD Cam Runtime Scripts

Папка содержит runtime-скрипты камеры для интеграции Klipper/Moonraker.

Скрипты:
- `session_start.sh` — открывает сессию снимков и делает стартовый кадр.
- `snapshot.sh` — сохраняет очередной снимок в активную сессию.
- `session_stop.sh` — завершает сессию (чистит маркер активной сессии).

Контракт интеграции:
- Moonraker shell_command:
  - `treed_cam_session_start`
  - `treed_cam_snapshot`
  - `treed_cam_session_stop`
- макросы Klipper вызывают эти команды через `action_call_remote_method`.

Деплой:
- источник: `runtime-scripts/treed-cam/*`
- раскладка: `/home/pi/treed/cam/bin/*`
- выполняет шаг: `loader/steps/treed-cam.sh`

