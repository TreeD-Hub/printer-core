# TreeD MainshellOS: модель владения конфигами и слоями

Документ фиксирует фактическую модель, реализованную в `loader/loader.sh`.
Если поведение в runtime и текст документа расходятся, источником истины считается код шагов loader.

## 1. Точки входа и порядок шагов

Entrypoint:

- `loader/loader.sh`

Полный порядок шагов:

1. `check-env`
2. `detect-rpi`
3. `timezone-sync`
4. `maintenance-stop`
5. `packages-core`
6. `boot-hdmi-config`
7. `rpi-uart-config`
8. `plymouth-theme-install`
9. `plymouth-initramfs`
10. `plymouth-initramfs-config`
11. `plymouth-cmdline`
12. `plymouth-systemd`
13. `klipper-sync`
14. `klipper-profiles`
15. `klipper-core`
16. `klipper-anti-shutdown`
17. `moonraker-config`
18. `crowsnest-webcam`
19. `treed-cam`
20. `klipper-mainsail-theme`
21. `klipperscreen-install`
22. `klipperscreen-theme`
23. `klipperscreen-integr`
24. `maintenance-start`
25. `verify`

## 2. Слои и source of truth

Repo (источник правды):

- `klipper/*`
- `moonraker/*`
- `runtime-scripts/*`
- `mainsail/.theme/*`
- `klipperscreen/themes/*`

Staging:

- `/home/pi/treed/klipper` (результат `klipper-sync`)

Runtime:

- `/home/pi/printer_data/config` (раскладка `klipper-core`)
- `/home/pi/treed/cam/bin` (раскладка `treed-cam`)
- `${TREED_KLIPPERSCREEN_HOME}/styles/treed-oled` (раскладка `klipperscreen-theme`)

## 3. Ownership map (runtime)

Управляется репозиторием и шагами loader:

- `/home/pi/printer_data/config/printer.cfg`
- `/home/pi/printer_data/config/profiles/*`
- `/home/pi/printer_data/config/moonraker.conf`
- `/home/pi/printer_data/config/moonraker/base/*.conf`
- `/home/pi/printer_data/config/.theme/*`

Генерируется loader-шагами:

- `/home/pi/printer_data/config/moonraker/generated/50-webcam-treed.conf`
  владелец: `loader/steps/crowsnest-webcam.sh`

Loader очищает старые `*.conf` в `moonraker/generated` (кроме `00-placeholder.conf`) и затем создает актуальные фрагменты.

Runtime-скрипты камеры:

- source: `runtime-scripts/treed-cam/*`
- deploy: `/home/pi/treed/cam/bin/*`
- владелец: `loader/steps/treed-cam.sh`

Тема KlipperScreen:

- source: `klipperscreen/themes/treed-oled/*`
- deploy: `${TREED_KLIPPERSCREEN_HOME}/styles/treed-oled/*`
- font deploy: `/usr/local/share/fonts/treed/web_ibm_mda.ttf`
- владелец: `loader/steps/klipperscreen-theme.sh`
- примечание: если в теме нет `images/`, шаг подбирает fallback icon-pack из доступной стоковой темы KlipperScreen так, чтобы закрыть обязательные `images/*`-ссылки из `style.css`.

По умолчанию `TREED_KLIPPERSCREEN_HOME` определяется из `WorkingDirectory` сервиса `KlipperScreen.service`, fallback — `/home/pi/KlipperScreen`.

## 4. Локальные override и TREED_DEPLOY_MODE

`TREED_DEPLOY_MODE` применяется только к runtime-конфиг шагам (`klipper-core`, `moonraker-config`, `klipperscreen-theme`):

- `clean`: локальные runtime-overrides не восстанавливаются, `.bak` для runtime-конфигов не создаются.
- `preserve`: сохраняется только `local_overrides.cfg`; для `moonraker.conf` и `KlipperScreen.conf` разрешен `backup_file_once`.
- `auto` (по умолчанию): `dev -> clean`, не-`dev` ветки -> `preserve`, неопределенная ветка (`HEAD`) -> `clean`.

Allowlist runtime-preserve:

- `local_overrides.cfg`

Важно:

- `local_overrides.cfg` гарантированно присутствует после `klipper-core` в любом режиме.
- `mainsail.cfg`, `timelapse.cfg`, `crowsnest.conf`, `KlipperScreen.conf`, `sonar.conf` больше не восстанавливаются через `klipper-core`.
- `moonraker.conf` в любом режиме деплоится канонической версией из репозитория.

## 5. Камера и fail policy

- По умолчанию отсутствие камеры не валит базовый provisioning.
- Строгий режим включается переменной `TREED_CAMERA_REQUIRED=1`.

## 6. Что не используется

- Нет активной цепочки `root.cfg` / `printer_root.cfg`.
- Не используется dynamic profile-switching через `profiles/current`.

## 7. Аудит delete/backup-политик

Mode-aware (зависит от `TREED_DEPLOY_MODE_EFFECTIVE`):

- `loader/steps/klipper-core.sh` — wipe runtime + restore only `local_overrides.cfg` в `preserve`.
- `loader/steps/moonraker-config.sh` — `backup_file_once` для `moonraker.conf` только в `preserve`.
- `loader/steps/klipperscreen-theme.sh` — `backup_file_once` для `KlipperScreen.conf` только в `preserve`.

Оставлено как есть (управляемые runtime-артефакты, не preserve):

- `loader/steps/klipper-sync.sh` — пересборка staging.
- `loader/steps/treed-cam.sh` — очистка `cam/bin` перед копированием.
- `loader/steps/klipper-mainsail-theme.sh` — `rsync --delete` для `.theme`.
- `loader/steps/crowsnest-webcam.sh` — prune `moonraker/generated/*.conf` и перегенерация webcam-фрагмента.
- `loader/steps/moonraker-config.sh` — очистка `moonraker/base` и `moonraker/generated` (кроме placeholder).

Системные шаги (не зависят от deploy-mode):

- `boot-hdmi-config.sh`
- `rpi-uart-config.sh`
- `plymouth-theme-install.sh`
- `plymouth-initramfs.sh`
- `plymouth-initramfs-config.sh`
- `plymouth-cmdline.sh`
- `plymouth-systemd.sh`
- `klipperscreen-integr.sh`
