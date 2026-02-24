# TreeD Cam Runtime Scripts

Папка содержит runtime-скрипты камеры для интеграции Klipper/Moonraker.

## Скрипты и поведение

- `cam_env.sh`
  - общий helper загрузки zoom-конфига (`zoom_profiles.env` + `zoom_active.env`);
  - валидирует активный профиль и ROI;
  - дает единый источник правды для URL/портов/ROI.
- `zoom-sidecar.sh`
  - long-running wrapper для backend-sidecar (crop/scale поверх raw crowsnest stream);
  - поднимает zoom-stream и обновляет zoom-snapshot;
  - делает hot-reload при смене `zoom_active.env`.
- `zoom_profile_set.sh`
  - переключает активный zoom-профиль (`wide|medium|close`);
  - пишет `zoom_active.env` атомарно;
  - используется из Moonraker/Klipper без `sudo`.
- `zoom_profile_status.sh`
  - печатает активный профиль и ключевые URL camera-контура для диагностики.
- `session_start.sh`
  - создает каталог новой сессии в `${PI_HOME}/treed/cam/prints`;
  - записывает путь активной сессии в marker-файл `/tmp/treed_cam_session_dir`;
  - делает стартовый снимок в best-effort режиме через zoom snapshot URL из общего конфига.
- `snapshot.sh`
  - проверяет наличие marker-файла сессии;
  - при активной сессии сохраняет очередной снимок в каталог сессии;
  - при отсутствии сессии/zoom-конфига/ошибке камеры завершает работу безопасно (`exit 0`).
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
  - `treed_cam_zoom_set`
  - `treed_cam_zoom_status`
- вызов идет из Klipper-макросов через `action_call_remote_method`.

## Runtime-конфиг и ownership

- `zoom_profiles.env`
  - путь: `${PI_HOME}/treed/cam/config/zoom_profiles.env`
  - владелец: `loader/steps/crowsnest-webcam.sh`
  - содержит raw/zoom URL, output size, список профилей и ROI.
- `zoom_active.env`
  - путь: `${PI_HOME}/treed/cam/config/zoom_active.env`
  - первично создается `loader/steps/crowsnest-webcam.sh`
  - runtime-владелец active-profile: `zoom_profile_set.sh`
  - меняется только через `zoom_profile_set.sh`.

## Деплой

- источник: `runtime-scripts/treed-cam/*`
- целевой путь: `${PI_HOME}/treed/cam/bin/*`
- выполняет шаг: `loader/steps/treed-cam.sh`

## Ограничения и замечания

- stream/snapshot URL берутся из `zoom_profiles.env`; отдельные скрипты не должны держать захардкоженные порты;
- marker-файл сессии хранится в `/tmp` и сбрасывается после reboot;
- скрипты рассчитаны на fail-safe поведение: не должны валить основной печатный контур.
