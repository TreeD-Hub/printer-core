# Скрипты runtime

Каталог `runtime-scripts/` содержит исходники runtime-скриптов, которые раскладываются на устройство шагом loader.

## Состав

- `runtime-scripts/treed-cam/*` — runtime-команды камеры TreeD.
- `runtime-scripts/treed-ui/*` — операторские команды переключения экранного UI.
- `runtime-scripts/treed-update/*` — root-side очередь, persistent operation state, UI bundle и пакет компонентов core с journal/rollback.
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
- команды на устройстве: `/usr/local/sbin/treed-update-service`, `/usr/local/sbin/treed-update-apply`, `/usr/local/sbin/treed-core-update`
- persistent operation state/history: `/var/lib/treed-update/state.json`
- core version/ownership: `/var/lib/treed-update/core-manifest.json`, после успешного loader verify или runtime update
- отдельный системный A/B контур: `runtime-scripts/treed-ab/README.md`

## Контракт runtime-скриптов

- скрипты выполняются в установленной системе, отдельно от шагов loader; `treed-ui` также предназначен для ручного вызова оператором;
- скрипты должны быть безопасны для повторного запуска (идемпотентность);
- имена файлов и контракт вызова должны оставаться совместимыми с Moonraker `shell_command`;
- сбои в получении кадра должны обрабатываться безопасно (без поломки печатного контура);
- переключатель UI должен оставлять активным только один экранный сервис: `treed-shell.service` или `KlipperScreen.service`.

## Где смотреть детали

- [Камера](treed-cam/README.md) — снимки, сессии и диагностический клиент.
- [Экранный UI](treed-ui/README.md) — команда `treed-ui`.
- [Обновления](treed-update/README.md) — очередь операций, установка пакета и откат.
- [Системный A/B-адаптер](treed-ab/README.md) — ограничения будущего системного обновления.
