# Runtime Scripts

Каталог `runtime-scripts/` содержит исходники runtime-скриптов, которые раскладываются на устройство шагом loader.

## Текущий scope

- `runtime-scripts/treed-cam/*` — runtime-команды камеры TreeD.
- `runtime-scripts/treed-ui/*` — операторские команды переключения экранного UI.
- `runtime-scripts/treed-update/*` — root-side очередь, persistent operation state и UI bundle update.
- `runtime-scripts/treed-ab/*` — fail-closed boundary для будущего A/B system updater.

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
- источник в репозитории: `runtime-scripts/treed-update/treed-update-service`, `treed-update-apply` и systemd units
- шаг деплоя: `loader/steps/moonraker-config.sh`
- команды на устройстве: `/usr/local/sbin/treed-update-service`, `/usr/local/sbin/treed-update-apply`
- persistent operation state/history: `/var/lib/treed-update/state.json`
- описание capability системы: `runtime-scripts/treed-ab/README.md`

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
- `runtime-scripts/treed-ab/README.md` — контракт fail-closed системного A/B адаптера.
