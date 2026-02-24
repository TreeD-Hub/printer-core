# Runtime Scripts

Каталог `runtime-scripts/` содержит исходники runtime-скриптов, которые раскладываются на устройство шагом loader.

## Текущий scope

- `runtime-scripts/treed-cam/*` — runtime-команды камеры TreeD.

## Модель деплоя

- источник в репозитории: `runtime-scripts/treed-cam/*`
- шаг деплоя: `loader/steps/treed-cam.sh`
- путь на устройстве: `${PI_HOME}/treed/cam/bin` (обычно `/home/pi/treed/cam/bin`)
- runtime-конфиг zoom-профилей: `${PI_HOME}/treed/cam/config/*.env`
- каталог данных снимков: `${PI_HOME}/treed/cam/prints`

## Контракт runtime-скриптов

- это не шаги loader и не операторские утилиты;
- скрипты должны быть безопасны для повторного запуска (идемпотентность);
- имена файлов и контракт вызова должны оставаться совместимыми с Moonraker `shell_command`;
- сбои в получении кадра должны обрабатываться безопасно (без поломки печатного контура).
- URL/порты камеры не хардкодятся в отдельных скриптах, а читаются из общего `zoom_profiles.env`.

## Где смотреть детали

- `runtime-scripts/treed-cam/README.md` — подробный контракт и поведение camera-скриптов.
