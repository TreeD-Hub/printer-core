# Runtime Scripts

Каталог `runtime-scripts/` содержит исходники runtime-скриптов, которые раскладываются на устройство шагом loader.

## Текущий scope

- `runtime-scripts/treed-cam/*` — runtime-команды камеры TreeD.
- `runtime-scripts/treed-ui/*` — операторские команды переключения экранного UI.
- `runtime-scripts/treed-update/*` — root-side применение release update для `treed-mainshellOS`.

## Модель деплоя

- источник в репозитории: `runtime-scripts/treed-cam/*`
- шаг деплоя: `loader/steps/treed-cam.sh`
- путь на устройстве: `${PI_HOME}/treed/cam/bin`
- каталог данных снимков: `${PI_HOME}/treed/cam/prints`

Для UI:
- источник в репозитории: `runtime-scripts/treed-ui/treed-ui`
- шаг деплоя: `loader/steps/treed-shell-install.sh`
- путь на устройстве: `/usr/local/sbin/treed-ui`
- persisted-состояние: `/etc/default/treed-ui`

Для обновлений:
- источник в репозитории: `runtime-scripts/treed-update/treed-update-apply`
- шаг деплоя: `loader/steps/moonraker-config.sh`
- путь на устройстве: `/usr/local/sbin/treed-update-apply`
- persisted-конфиг: `/etc/default/treed-update`

## Контракт runtime-скриптов

- это не шаги loader и не операторские утилиты;
- скрипты должны быть безопасны для повторного запуска (идемпотентность);
- имена файлов и контракт вызова должны оставаться совместимыми с Moonraker `shell_command`;
- сбои в получении кадра должны обрабатываться безопасно (без поломки печатного контура);
- переключатель UI должен оставлять активным только один экранный сервис: `treed-shell.service` или `KlipperScreen.service`.

## Где смотреть детали

- `runtime-scripts/treed-cam/README.md` — подробный контракт и поведение camera-скриптов.
- `runtime-scripts/treed-ui/README.md` — контракт команды `treed-ui`.
- `runtime-scripts/treed-update/README.md` — контракт root-side updater.
